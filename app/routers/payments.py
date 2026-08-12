"""Transaction history — straight from the wallet ledger.

Previously this was proxied from Boostyfi for their users and synthesised from
the generation list for everyone else. Now there is one honest source: the
append-only journal that every charge, grant and refund is written to, so what
the screen shows and what the balance is made of are the same rows.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import models as m
from app.db.session import get_session as get_db_session
from app.deps import Context, required_context

router = APIRouter(prefix="/api", tags=["payments"])


@router.get("/transactions")
async def transactions(
    ctx: Context = Depends(required_context),
    db: AsyncSession = Depends(get_db_session),
    limit: int = Query(default=100, ge=1, le=500),
) -> list[dict]:
    """Wallet movements, newest first.

    ``bucket`` is included because a charge can span both buckets and produce
    two rows: hiding that would make the arithmetic on screen look wrong.
    """
    user, _ = ctx
    stmt = (
        select(m.WalletLedger)
        .where(m.WalletLedger.user_id == user.id)
        .order_by(m.WalletLedger.created_at.desc(), m.WalletLedger.id.desc())
        .limit(limit)
    )
    rows = (await db.scalars(stmt)).all()
    return [
        {
            "payment_id": row.ref_id,
            "type": row.reason,
            "bucket": row.bucket,
            "amount": row.delta,
            "balance_after": row.balance_after,
            "created_at": row.created_at.isoformat(),
        }
        for row in rows
    ]
