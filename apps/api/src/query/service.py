"""
CloudCarbon Natural Language Query Service (synchronous).
Uses Anthropic Claude to translate natural language to SQL.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Optional
from uuid import UUID

import anthropic
from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

client = anthropic.Anthropic()

SCHEMA_CONTEXT = """
You have access to two primary tables for querying CloudCarbon data.

TABLE: enriched_records
This is the main analytics table. Each row represents one cloud billing line item with all enrichment applied.

Columns:
- id: UUID, primary key
- tenant_id: UUID, always filter by this, value provided in system context
- focus_record_id: UUID, links to the raw billing record
- billing_period_start: timestamp, when the billing period started
- billing_period_end: timestamp, when the billing period ended
- provider: text, one of: aws, azure, gcp, alibaba
- service_name: text, the cloud service name (e.g. Amazon EC2, Azure Virtual Machines)
- service_category: text, one of: Compute, Storage, Networking, Database, AI and Machine Learning, Other
- region: text, cloud region identifier (e.g. us-east-1, eastus, us-central1)
- resource_id: text, unique resource identifier
- resource_type: text, resource type (e.g. m5.large, Standard_D4s_v3)
- cost_usd: numeric, actual cost in US dollars
- usage_quantity: numeric, amount of resource consumed
- usage_unit: text, unit of consumption
- scope1_co2e_kg: numeric, direct emissions in kg CO2 equivalent (always 0 for cloud)
- scope2_co2e_kg_location: numeric, location-based Scope 2 emissions in kg CO2e
- scope2_co2e_kg_market: numeric, market-based Scope 2 emissions in kg CO2e
- scope3_cat1_co2e_kg: numeric, embodied carbon (hardware manufacturing) in kg CO2e
- scope3_cat3_co2e_kg: numeric, upstream energy supply chain in kg CO2e
- scope3_cat12_co2e_kg: numeric, end-of-life hardware treatment in kg CO2e
- scope3_total_co2e_kg: numeric, sum of all Scope 3 categories in kg CO2e
- scope3_confidence: text, one of: high, medium, low
- total_co2e_kg: numeric, total emissions (scope2_location + scope3_total) in kg CO2e
- carbon_intensity_gco2_kwh: numeric, grid carbon intensity in gCO2/kWh
- estimated_kwh: numeric, estimated electricity consumed in kWh
- hardware_family: text, resolved hardware class (e.g. intel_xeon_cascade_lake)
- water_litres: numeric, estimated water consumption in litres
- water_stress_score: numeric, WRI Aqueduct water stress 0-5 scale (5 = extreme stress)
- water_stress_adjusted_litres: numeric, water consumption weighted by regional stress
- enriched_at: timestamp, when enrichment was run

TABLE: focus_records
Raw normalized billing records before enrichment. Use enriched_records for most queries.
Key columns: id, tenant_id, provider, service_name, region, resource_id, cost_usd, billing_period_start

COMMON QUERY PATTERNS:

Total spend by provider:
SELECT provider, SUM(cost_usd) as total_cost FROM enriched_records WHERE tenant_id = '{tenant_id}' GROUP BY provider ORDER BY total_cost DESC

Top services by carbon:
SELECT service_name, SUM(total_co2e_kg) as total_carbon FROM enriched_records WHERE tenant_id = '{tenant_id}' GROUP BY service_name ORDER BY total_carbon DESC LIMIT 10

Monthly cost trend:
SELECT DATE_TRUNC('month', billing_period_start) as month, SUM(cost_usd) as cost, SUM(total_co2e_kg) as co2e FROM enriched_records WHERE tenant_id = '{tenant_id}' GROUP BY month ORDER BY month

Water consumption by region:
SELECT region, provider, SUM(water_litres) as water, AVG(water_stress_score) as avg_stress FROM enriched_records WHERE tenant_id = '{tenant_id}' GROUP BY region, provider ORDER BY water DESC

Scope 3 breakdown:
SELECT SUM(scope3_cat1_co2e_kg) as embodied, SUM(scope3_cat3_co2e_kg) as upstream, SUM(scope3_cat12_co2e_kg) as eol FROM enriched_records WHERE tenant_id = '{tenant_id}'

Carbon efficiency (kg CO2e per $1000 spend):
SELECT provider, (SUM(total_co2e_kg) / SUM(cost_usd) * 1000) as kg_per_1000_usd FROM enriched_records WHERE tenant_id = '{tenant_id}' GROUP BY provider
"""


@dataclass
class NLQueryResult:
    supported: bool
    question: str
    sql: Optional[str] = None
    results: Optional[list[dict]] = None
    row_count: int = 0
    suggestions: Optional[list[str]] = None
    error: Optional[str] = None


def natural_language_to_sql(question: str, tenant_id: UUID, db: Session) -> NLQueryResult:
    tenant_str = str(tenant_id)

    system_prompt = f"""You are a PostgreSQL query assistant for CloudCarbon, a multi-cloud sustainability platform.

Your job: translate the user's natural language question into a single valid PostgreSQL SELECT query.

Rules you must follow:
1. Only generate SELECT statements. Never INSERT, UPDATE, DELETE, DROP, ALTER, or any DDL.
2. Always include WHERE tenant_id = '{tenant_str}' in every query.
3. Return ONLY the SQL query, nothing else. No explanation, no markdown, no backticks.
4. If the question cannot be answered with the available schema, return exactly: UNSUPPORTED
5. Always use table aliases for readability.
6. Limit results to 1000 rows maximum using LIMIT 1000 unless the user asks for a specific limit.
7. For date filtering, use billing_period_start column.
8. All monetary values are in USD. All carbon values are in kg CO2e. All water values are in litres.

{SCHEMA_CONTEXT}"""

    try:
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=500,
            system=system_prompt,
            messages=[{"role": "user", "content": question}],
        )
        sql = response.content[0].text.strip()
    except Exception as exc:
        logger.error("LLM call failed: %s", exc)
        return NLQueryResult(supported=False, question=question, error=f"LLM unavailable: {exc}")

    if sql.upper() == "UNSUPPORTED":
        return NLQueryResult(supported=False, question=question, sql=None, results=[], row_count=0, suggestions=[])

    # Strip markdown fences if model adds them despite instructions
    sql = re.sub(r"^```(?:sql)?\s*", "", sql, flags=re.IGNORECASE)
    sql = re.sub(r"\s*```$", "", sql).strip()

    sql_upper = sql.upper().strip()
    if not sql_upper.startswith("SELECT"):
        return NLQueryResult(
            supported=False, question=question, sql=None, results=[], row_count=0, suggestions=[],
            error="Generated query was not a SELECT statement",
        )

    # Re-inject tenant filter if Claude dropped it
    if tenant_str not in sql:
        sql = sql.rstrip(";")
        if "WHERE" in sql_upper:
            sql = sql + f" AND tenant_id = '{tenant_str}'"
        else:
            sql = sql + f" WHERE tenant_id = '{tenant_str}'"

    # Remove semicolons to prevent statement chaining
    sql = sql.replace(";", "")

    # Reject any non-SELECT statements (secondary safety check)
    dangerous = re.compile(
        r"\b(INSERT|UPDATE|DELETE|DROP|CREATE|ALTER|TRUNCATE|GRANT|REVOKE|EXEC|EXECUTE)\b",
        re.IGNORECASE,
    )
    if dangerous.search(sql):
        return NLQueryResult(
            supported=False, question=question, sql=None, results=[], row_count=0, suggestions=[],
            error="Generated query contained a forbidden keyword",
        )

    # Execute with PostgreSQL statement timeout
    try:
        result = db.execute(text(f"SET LOCAL statement_timeout = '10s'; {sql}"))
        rows = result.fetchall()
        columns = list(result.keys())
        results = [dict(zip(columns, row)) for row in rows]
    except Exception as exc:
        return NLQueryResult(
            supported=True, question=question, sql=sql, results=[], row_count=0,
            error=str(exc), suggestions=[],
        )

    suggestions = generate_followup_suggestions(question, len(results), columns)

    return NLQueryResult(
        supported=True,
        question=question,
        sql=sql,
        results=results,
        row_count=len(results),
        suggestions=suggestions,
    )


def generate_followup_suggestions(question: str, result_count: int, columns: list) -> list[str]:
    try:
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=200,
            messages=[{
                "role": "user",
                "content": (
                    f'The user asked: "{question}"\n'
                    f"The query returned {result_count} rows with columns: {', '.join(columns)}.\n\n"
                    "Generate exactly 3 short follow-up questions the user might want to ask next.\n"
                    'Return them as a JSON array of strings, nothing else.\n'
                    'Example: ["Which region has the highest carbon intensity?", '
                    '"How has this changed over the last 3 months?", '
                    '"Which team owns the most expensive resources?"]'
                ),
            }],
        )
        return json.loads(response.content[0].text.strip())
    except Exception:
        return [
            "How has this changed over the last 30 days?",
            "Which provider contributes most to this metric?",
            "What are the top optimization opportunities?",
        ]


# Keep the old name as an alias so the router import still works
def suggest_followup_questions(question: str, results_summary: str) -> list[str]:
    return generate_followup_suggestions(question, 0, [])
