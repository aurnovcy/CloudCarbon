"""
CloudCarbon Natural Language Query Service (synchronous).
Uses Anthropic Claude to translate natural language to SQL.
"""
from __future__ import annotations

import logging
import os
import re
import threading
from dataclasses import dataclass
from typing import Any, Optional
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

_MAX_TOKENS = 500
_QUERY_TIMEOUT_SECONDS = 10
_MAX_RESULT_ROWS = 1000

_SCHEMA_CONTEXT = """
## Database Schema

### Table: focus_records
| Column | Type | Description |
|--------|------|-------------|
| id | UUID | Primary key |
| tenant_id | UUID | Tenant identifier |
| provider_name | TEXT | Cloud provider: AWS, Azure, GCP, Alibaba |
| service_name | TEXT | Cloud service name |
| service_category | TEXT | Service category |
| region_id | TEXT | Region identifier |
| resource_id | TEXT | Cloud resource identifier |
| resource_name | TEXT | Human-readable resource name |
| effective_cost | NUMERIC | Effective cost in USD |
| charge_period_start | TIMESTAMPTZ | Start of charge period |
| charge_period_end | TIMESTAMPTZ | End of charge period |

### Table: enriched_records
| Column | Type | Description |
|--------|------|-------------|
| id | UUID | Primary key |
| focus_record_id | UUID | Foreign key to focus_records.id |
| tenant_id | UUID | Tenant identifier |
| total_co2e_kg | NUMERIC | Total carbon all scopes (kg CO2e) |
| scope3_total_co2e_kg | NUMERIC | Total Scope 3 carbon |
| water_litres | NUMERIC | Estimated water consumption |
| water_stress_adjusted_litres | NUMERIC | Water adjusted for regional stress |

### Common Join Pattern
SELECT fr.*, er.* FROM focus_records fr
JOIN enriched_records er ON er.focus_record_id = fr.id
WHERE fr.tenant_id = '{tenant_id}'
"""

_SYSTEM_PROMPT = """You are a data query assistant for CloudCarbon.
Translate natural language questions into PostgreSQL SELECT queries.
Rules:
1. ONLY generate SELECT statements.
2. Always include tenant_id = '{tenant_id}' in WHERE clause.
3. Return ONLY the SQL query, no explanation.
4. If question cannot be answered, return: UNSUPPORTED
5. Never use semicolons.
6. Use table aliases (fr for focus_records, er for enriched_records).
7. LIMIT results to 1000 rows."""


@dataclass
class NLQueryResult:
    supported: bool
    question: str
    sql: Optional[str] = None
    results: Optional[list[dict]] = None
    row_count: int = 0
    suggestions: Optional[list[str]] = None
    error: Optional[str] = None


def _validate_sql(sql: str, tenant_id: UUID) -> Optional[str]:
    sql_stripped = sql.strip()
    if not sql_stripped.upper().startswith("SELECT"):
        return "Generated SQL does not start with SELECT"
    if ";" in sql_stripped:
        return "SQL contains semicolon"
    dangerous = re.compile(r"\b(INSERT|UPDATE|DELETE|DROP|CREATE|ALTER|TRUNCATE|GRANT|REVOKE|EXEC|EXECUTE)\b", re.IGNORECASE)
    match = dangerous.search(sql_stripped)
    if match:
        return f"SQL contains forbidden keyword: {match.group()}"
    if str(tenant_id) not in sql_stripped and "tenant_id" not in sql_stripped.lower():
        return "SQL does not contain tenant_id filter"
    return None


def _call_llm(messages: list[dict], max_tokens: int = _MAX_TOKENS) -> str:
    """Call Anthropic Claude and return the response text."""
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY", ""))

        # Convert from OpenAI-style to Anthropic-style
        system_msg = ""
        user_messages = []
        for msg in messages:
            if msg["role"] == "system":
                system_msg = msg["content"]
            else:
                user_messages.append({"role": msg["role"], "content": msg["content"]})

        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=max_tokens,
            system=system_msg,
            messages=user_messages,
        )
        return response.content[0].text.strip()
    except Exception as exc:
        raise RuntimeError(f"LLM call failed: {exc}") from exc


def natural_language_to_sql(
    question: str, tenant_id: UUID, db: Session,
) -> NLQueryResult:
    tenant_str = str(tenant_id)
    schema = _SCHEMA_CONTEXT.replace("{tenant_id}", tenant_str)
    system = _SYSTEM_PROMPT.replace("{tenant_id}", tenant_str)

    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": f"Schema:\n{schema}\n\nQuestion: {question}"},
    ]

    try:
        raw_sql = _call_llm(messages)
    except Exception as exc:
        logger.error("LLM call failed: %s", exc)
        return NLQueryResult(supported=False, question=question, error=f"LLM unavailable: {exc}")

    if raw_sql.strip().upper() == "UNSUPPORTED":
        return NLQueryResult(supported=False, question=question)

    sql = re.sub(r"^```(?:sql)?\s*", "", raw_sql, flags=re.IGNORECASE)
    sql = re.sub(r"\s*```$", "", sql).strip()

    validation_error = _validate_sql(sql, tenant_id)
    if validation_error:
        return NLQueryResult(supported=False, question=question, sql=sql, error=f"SQL validation failed: {validation_error}")

    # Execute with timeout via threading
    result_container: dict[str, Any] = {}

    def _execute():
        try:
            res = db.execute(text(sql))
            rows = res.fetchmany(_MAX_RESULT_ROWS)
            columns = list(res.keys()) if res.keys() else []
            result_container["results"] = [dict(zip(columns, row)) for row in rows]
        except Exception as exc:
            result_container["error"] = str(exc)

    t = threading.Thread(target=_execute)
    t.start()
    t.join(timeout=_QUERY_TIMEOUT_SECONDS)

    if t.is_alive():
        return NLQueryResult(supported=True, question=question, sql=sql, results=[], row_count=0, error="Query timed out after 10 seconds")

    if "error" in result_container:
        return NLQueryResult(supported=True, question=question, sql=sql, results=[], row_count=0, error=f"Query execution failed: {result_container['error']}")

    results = result_container.get("results", [])
    return NLQueryResult(supported=True, question=question, sql=sql, results=results, row_count=len(results))


def suggest_followup_questions(question: str, results_summary: str) -> list[str]:
    messages = [
        {"role": "system", "content": "You are a helpful data analyst for CloudCarbon. Generate exactly 3 concise follow-up questions. Return only the 3 questions, one per line, no numbering or bullets."},
        {"role": "user", "content": f"Original question: {question}\n\nResults summary: {results_summary}\n\nSuggest 3 follow-up questions:"},
    ]
    try:
        response = _call_llm(messages, max_tokens=200)
        lines = [line.strip() for line in response.split("\n") if line.strip()]
        return lines[:3]
    except Exception:
        return [
            "What is the trend over the last 3 months?",
            "Which region has the highest carbon intensity?",
            "What are the top 5 resources by cost?",
        ]
