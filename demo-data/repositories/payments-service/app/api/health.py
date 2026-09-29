from fastapi import APIRouter
from sqlalchemy import text

from app.db import session_scope

router = APIRouter(tags=["ops"])


@router.get("/healthz")
def healthz() -> dict[str, str]:
    # ADR-003 exception: operational endpoints check infrastructure directly.
    with session_scope() as session:
        session.execute(text("SELECT 1"))
    return {"status": "ok"}
