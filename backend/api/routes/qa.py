"""POST /api/qa/ask — natural-language Q&A over the tenant's books.

Body:
  { "question": "How much did I spend on AWS last quarter?" }

Response:
  {
    "question": "...",
    "sql": "SELECT ... WHERE org_id = :org_id ...",  // for transparency
    "row_count": 12,
    "sample": [ { ...row... }, ... ],                // up to 200 rows
    "answer": "You spent ₹X.XX L on AWS last quarter — peak month was..."
  }

Errors return HTTP 400 with a `detail` explaining what went wrong. Safety
violations (LLM proposed a non-SELECT or referenced a banned table) bubble
up as 400s — we never run unsafe SQL even if Claude tries.
"""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from api.deps import current_org_id
from common.db import get_db
from services.qa import QAError, QAOverloadedError, ask, is_enabled as _qa_enabled

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/qa", tags=["qa"])


class AskIn(BaseModel):
    question: str = Field(min_length=1, max_length=1000)


class AskOut(BaseModel):
    question: str
    sql: str | None
    row_count: int
    sample: list[dict]
    answer: str


@router.post("/ask", response_model=AskOut, summary="Ask a question about your books")
def ask_question(
    body: AskIn,
    db: Session = Depends(get_db),
    org_id: uuid.UUID = Depends(current_org_id),
) -> AskOut:
    if not _qa_enabled():
        # Soft-fail with a friendly note — same shape as a successful response
        # so the frontend doesn't need a special branch.
        return AskOut(
            question=body.question,
            sql=None,
            row_count=0,
            sample=[],
            answer=(
                "Q&A is disabled on this server because ANTHROPIC_API_KEY "
                "isn't set. Once it's configured, ask me anything about "
                "your bank transactions, invoices, receipts, or vendors."
            ),
        )

    try:
        result = ask(body.question, org_id=org_id, db=db)
    except QAOverloadedError:
        # Anthropic rate-limited / overloaded. Return a 503-shaped soft-fail
        # so the frontend can show a "try again" hint instead of a red error.
        return AskOut(
            question=body.question,
            sql=None,
            row_count=0,
            sample=[],
            answer=(
                "Claude is a bit overloaded right now — please try the same "
                "question again in 20-30 seconds. If this keeps happening, "
                "Anthropic's status page (status.anthropic.com) will say so."
            ),
        )
    except QAError as e:
        # Treat as user-facing 400 — the LLM proposed bad SQL or the question
        # couldn't be answered. The error message is safe to surface.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Couldn't answer that — {e}",
        )
    except Exception as e:  # noqa: BLE001
        # Unexpected — log it, return a 500 with a sanitized message.
        logger.exception("qa.ask unexpected error")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal error while answering — please try again.",
        )

    return AskOut(**result)
