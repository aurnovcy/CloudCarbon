"""
Natural Language Query router.

Endpoints:
  POST /query/natural-language — translate NL question to SQL and execute
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from src.database import get_db
from src.dependencies.auth import require_role
from src.query.service import natural_language_to_sql, suggest_followup_questions

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/query", tags=["Query"])


class NLQueryRequest(BaseModel):
    question: str = Field(..., min_length=5, max_length=1000)


class NLQueryResponse(BaseModel):
    question: str
    sql: Optional[str]
    results: Optional[list[dict]]
    row_count: int
    suggestions: list[str]
    supported: bool
    error: Optional[str] = None


@router.post("/natural-language", response_model=NLQueryResponse)
async def natural_language_query(
    body: NLQueryRequest,
    current_user=Depends(require_role("analyst")),
    db: AsyncSession = Depends(get_db),
):
    """
    Translate a natural language question into a SQL query and execute it.

    Returns the generated SQL, query results, and 3 follow-up question suggestions.

    **Required role:** analyst or above.

    **Example questions:**
    - "What is my total cloud spend by provider this month?"
    - "Which services have the highest carbon emissions?"
    - "Show me water consumption by region for AWS"
    - "What is my Scope 3 carbon breakdown?"
    """
    tenant_id = current_user.tenant_id

    result = await natural_language_to_sql(
        question=body.question,
        tenant_id=tenant_id,
        db=db,
    )

    # Generate follow-up suggestions
    if result.supported and result.results is not None:
        results_summary = (
            f"{result.row_count} rows returned"
            if result.row_count > 0
            else "No results found"
        )
        suggestions = await suggest_followup_questions(body.question, results_summary)
    else:
        suggestions = [
            "What is my total cloud spend by provider this month?",
            "Which services have the highest carbon emissions?",
            "Show me water consumption by region for AWS",
        ]

    return NLQueryResponse(
        question=body.question,
        sql=result.sql,
        results=result.results,
        row_count=result.row_count,
        suggestions=suggestions,
        supported=result.supported,
        error=result.error,
    )
