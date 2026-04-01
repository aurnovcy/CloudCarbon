"""
CloudCarbon Natural Language Query Service.

Translates natural language questions into PostgreSQL SELECT queries
against the enriched_records and focus_records tables using an LLM.

The service:
  1. Builds a schema context string describing both tables
  2. Sends the question to the LLM with a strict system prompt
  3. Validates the returned SQL (SELECT only, tenant_id filter, no multi-statement)
  4. Executes the query with a 10-second timeout
  5. Returns results + 3 follow-up question suggestions

Uses the OpenAI-compatible API endpoint configured via OPENAI_API_KEY.
Model: gemini-2.5-flash (fast, cost-effective for SQL generation).
"""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Any, Optional
from uuid import UUID

from openai import AsyncOpenAI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

_LLM_MODEL = "gemini-2.5-flash"
_MAX_TOKENS = 500
_QUERY_TIMEOUT_SECONDS = 10
_MAX_RESULT_ROWS = 1000

# ---------------------------------------------------------------------------
# Schema context (injected into every LLM prompt)
# ---------------------------------------------------------------------------

_SCHEMA_CONTEXT = """
## Database Schema

### Table: focus_records
Stores normalised FOCUS 1.0 billing records from all cloud providers.

| Column | Type | Description |
|--------|------|-------------|
| id | UUID | Primary key |
| tenant_id | UUID | Tenant identifier (always filter by this) |
| provider_name | TEXT | Cloud provider: AWS, Azure, GCP, Alibaba |
| service_name | TEXT | Cloud service name (e.g. Amazon EC2, Azure Virtual Machines) |
| service_category | TEXT | Service category (Compute, Storage, Database, Networking, etc.) |
| region_id | TEXT | Region identifier (e.g. us-east-1, eastus, us-central1) |
| resource_id | TEXT | Cloud resource identifier |
| resource_name | TEXT | Human-readable resource name |
| charge_category | TEXT | FOCUS charge category: Usage, Purchase, Tax, Adjustment, Credit |
| effective_cost | NUMERIC | Effective cost in USD |
| list_cost | NUMERIC | List (on-demand) cost in USD |
| billing_period_start | TIMESTAMPTZ | Start of billing period |
| billing_period_end | TIMESTAMPTZ | End of billing period |
| charge_period_start | TIMESTAMPTZ | Start of charge period |
| charge_period_end | TIMESTAMPTZ | End of charge period |
| tags | JSONB | Resource tags as key-value pairs |
| created_at | TIMESTAMPTZ | Record creation timestamp |

### Table: enriched_records
One row per focus_record with carbon and water enrichment data.

| Column | Type | Description |
|--------|------|-------------|
| id | UUID | Primary key |
| focus_record_id | UUID | Foreign key to focus_records.id |
| tenant_id | UUID | Tenant identifier (always filter by this) |
| scope2_co2e_kg_location | NUMERIC | Scope 2 location-based carbon (kg CO2e) |
| scope2_co2e_kg_market | NUMERIC | Scope 2 market-based carbon (kg CO2e) |
| scope3_cat1_co2e_kg | NUMERIC | Scope 3 Cat1 embodied hardware carbon (kg CO2e) |
| scope3_cat3_co2e_kg | NUMERIC | Scope 3 Cat3 upstream energy carbon (kg CO2e) |
| scope3_cat12_co2e_kg | NUMERIC | Scope 3 Cat12 end-of-life carbon (kg CO2e) |
| scope3_total_co2e_kg | NUMERIC | Total Scope 3 carbon (kg CO2e) |
| total_co2e_kg | NUMERIC | Total carbon all scopes (kg CO2e) |
| carbon_intensity_gco2_kwh | NUMERIC | Grid carbon intensity (gCO2/kWh) |
| estimated_kwh | NUMERIC | Estimated energy consumption (kWh) |
| water_litres | NUMERIC | Estimated water consumption (litres) |
| water_stress_adjusted_litres | NUMERIC | Water adjusted for regional stress (litres) |
| water_stress_score | NUMERIC | WRI Aqueduct water stress score (0-5) |
| enriched_at | TIMESTAMPTZ | Enrichment timestamp |
| enrichment_version | TEXT | Methodology version |

### Common Join Pattern
```sql
SELECT fr.*, er.*
FROM focus_records fr
JOIN enriched_records er ON er.focus_record_id = fr.id
WHERE fr.tenant_id = '{tenant_id}'
```

### Example Queries

Q: "What is my total cloud spend by provider this month?"
```sql
SELECT fr.provider_name, SUM(fr.effective_cost) AS total_cost_usd
FROM focus_records fr
WHERE fr.tenant_id = '{tenant_id}'
  AND fr.charge_period_start >= DATE_TRUNC('month', CURRENT_DATE)
GROUP BY fr.provider_name
ORDER BY total_cost_usd DESC;
```

Q: "Which services have the highest carbon emissions?"
```sql
SELECT fr.service_name, SUM(er.total_co2e_kg) AS total_co2e_kg
FROM focus_records fr
JOIN enriched_records er ON er.focus_record_id = fr.id
WHERE fr.tenant_id = '{tenant_id}'
GROUP BY fr.service_name
ORDER BY total_co2e_kg DESC
LIMIT 10;
```

Q: "Show me water consumption by region for AWS"
```sql
SELECT fr.region_id, SUM(er.water_litres) AS water_litres,
       SUM(er.water_stress_adjusted_litres) AS stress_adjusted_litres
FROM focus_records fr
JOIN enriched_records er ON er.focus_record_id = fr.id
WHERE fr.tenant_id = '{tenant_id}'
  AND fr.provider_name = 'AWS'
GROUP BY fr.region_id
ORDER BY water_litres DESC;
```

Q: "What is my Scope 3 carbon breakdown?"
```sql
SELECT
    SUM(er.scope3_cat1_co2e_kg) AS cat1_embodied_kg,
    SUM(er.scope3_cat3_co2e_kg) AS cat3_upstream_kg,
    SUM(er.scope3_cat12_co2e_kg) AS cat12_eol_kg,
    SUM(er.scope3_total_co2e_kg) AS total_scope3_kg
FROM focus_records fr
JOIN enriched_records er ON er.focus_record_id = fr.id
WHERE fr.tenant_id = '{tenant_id}';
```
"""

_SYSTEM_PROMPT = """You are a data query assistant for CloudCarbon, a multi-cloud sustainability platform.
You translate natural language questions into PostgreSQL SELECT queries against the enriched_records and focus_records tables.

Rules:
1. You must ONLY generate SELECT statements — never INSERT, UPDATE, DELETE, or DDL.
2. Always include tenant_id = '{tenant_id}' in the WHERE clause.
3. Return ONLY the SQL query, nothing else — no explanation, no markdown, no code fences.
4. If the question cannot be answered with the available data, return exactly: UNSUPPORTED
5. Never use semicolons (;) in the query.
6. Always use table aliases (fr for focus_records, er for enriched_records).
7. Limit results to 1000 rows maximum using LIMIT 1000 unless the user specifies a different limit."""


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class NLQueryResult:
    supported: bool
    question: str
    sql: Optional[str] = None
    results: Optional[list[dict]] = None
    row_count: int = 0
    suggestions: Optional[list[str]] = None
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# SQL validation
# ---------------------------------------------------------------------------

def _validate_sql(sql: str, tenant_id: UUID) -> Optional[str]:
    """
    Validate generated SQL.

    Returns an error message string if invalid, or None if valid.
    """
    sql_stripped = sql.strip()

    if not sql_stripped.upper().startswith("SELECT"):
        return "Generated SQL does not start with SELECT"

    if ";" in sql_stripped:
        return "SQL contains semicolon (multi-statement not allowed)"

    # Check for dangerous keywords
    dangerous = re.compile(
        r"\b(INSERT|UPDATE|DELETE|DROP|CREATE|ALTER|TRUNCATE|GRANT|REVOKE|EXEC|EXECUTE)\b",
        re.IGNORECASE,
    )
    match = dangerous.search(sql_stripped)
    if match:
        return f"SQL contains forbidden keyword: {match.group()}"

    # Check tenant_id filter is present
    tenant_str = str(tenant_id)
    if tenant_str not in sql_stripped and "tenant_id" not in sql_stripped.lower():
        return "SQL does not contain tenant_id filter"

    return None


# ---------------------------------------------------------------------------
# LLM calls
# ---------------------------------------------------------------------------

async def _call_llm(messages: list[dict], max_tokens: int = _MAX_TOKENS) -> str:
    """Call the LLM and return the response text."""
    client = AsyncOpenAI()
    response = await client.chat.completions.create(
        model=_LLM_MODEL,
        messages=messages,
        max_tokens=max_tokens,
        temperature=0.0,
    )
    return response.choices[0].message.content.strip()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def natural_language_to_sql(
    question: str,
    tenant_id: UUID,
    db: AsyncSession,
) -> NLQueryResult:
    """
    Translate a natural language question to SQL and execute it.

    Args:
        question: Natural language question from the user.
        tenant_id: Tenant UUID (injected into SQL and system prompt).
        db: AsyncSession for query execution.

    Returns:
        NLQueryResult with sql, results, row_count, and supported flag.
    """
    tenant_str = str(tenant_id)
    schema = _SCHEMA_CONTEXT.replace("{tenant_id}", tenant_str)
    system = _SYSTEM_PROMPT.replace("{tenant_id}", tenant_str)

    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": f"Schema:\n{schema}\n\nQuestion: {question}"},
    ]

    try:
        raw_sql = await _call_llm(messages)
    except Exception as exc:
        logger.error("LLM call failed: %s", exc)
        return NLQueryResult(
            supported=False,
            question=question,
            error=f"LLM unavailable: {exc}",
        )

    # Handle unsupported
    if raw_sql.strip().upper() == "UNSUPPORTED":
        return NLQueryResult(
            supported=False,
            question=question,
            sql=None,
        )

    # Strip any accidental markdown fences
    sql = re.sub(r"^```(?:sql)?\s*", "", raw_sql, flags=re.IGNORECASE)
    sql = re.sub(r"\s*```$", "", sql).strip()

    # Validate
    validation_error = _validate_sql(sql, tenant_id)
    if validation_error:
        logger.warning("SQL validation failed: %s | SQL: %s", validation_error, sql)
        return NLQueryResult(
            supported=False,
            question=question,
            sql=sql,
            error=f"SQL validation failed: {validation_error}",
        )

    # Execute with timeout
    try:
        async with asyncio.timeout(_QUERY_TIMEOUT_SECONDS):
            result = await db.execute(text(sql))
            rows = result.fetchmany(_MAX_RESULT_ROWS)
            columns = list(result.keys()) if result.keys() else []
            results = [dict(zip(columns, row)) for row in rows]
    except TimeoutError:
        return NLQueryResult(
            supported=True,
            question=question,
            sql=sql,
            results=[],
            row_count=0,
            error="Query timed out after 10 seconds",
        )
    except Exception as exc:
        logger.error("Query execution failed: %s | SQL: %s", exc, sql)
        return NLQueryResult(
            supported=True,
            question=question,
            sql=sql,
            results=[],
            row_count=0,
            error=f"Query execution failed: {exc}",
        )

    return NLQueryResult(
        supported=True,
        question=question,
        sql=sql,
        results=results,
        row_count=len(results),
    )


async def suggest_followup_questions(
    question: str,
    results_summary: str,
) -> list[str]:
    """
    Generate 3 follow-up question suggestions based on the original question and results.

    Args:
        question: The original natural language question.
        results_summary: A brief summary of the query results.

    Returns:
        List of 3 follow-up question strings.
    """
    messages = [
        {
            "role": "system",
            "content": (
                "You are a helpful data analyst assistant for CloudCarbon, "
                "a multi-cloud sustainability platform. "
                "Generate exactly 3 concise follow-up questions that would help "
                "the user explore their cloud cost and carbon data further. "
                "Return only the 3 questions, one per line, no numbering or bullets."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Original question: {question}\n\n"
                f"Results summary: {results_summary}\n\n"
                "Suggest 3 follow-up questions:"
            ),
        },
    ]

    try:
        response = await _call_llm(messages, max_tokens=200)
        lines = [line.strip() for line in response.split("\n") if line.strip()]
        return lines[:3]
    except Exception as exc:
        logger.warning("Follow-up suggestion failed: %s", exc)
        return [
            "What is the trend over the last 3 months?",
            "Which region has the highest carbon intensity?",
            "What are the top 5 resources by cost?",
        ]
