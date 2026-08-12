"""Tariffs and the daily reward.

Everything the paywall and the balance pill need, with the numbers coming from
the database rather than the build — pricing changes on a product schedule, and
an app release should not be the cost of changing a price (docs/ECONOMY.md).
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.repositories import plans as plans_repo
from app.db.repositories import wallet as wallet_repo
from app.db.session import get_session as get_db_session
from app.deps import Context, required_context

router = APIRouter(prefix="/api", tags=["billing"])


class Plan(BaseModel):
    id: str
    product_id: str
    title: str
    billing_period: str
    price_usd_cents: Optional[int]
    weekly_quota: int


class RewardResult(BaseModel):
    granted: int
    streak: int
    balance: int
    free_balance: int
    sub_balance: int
    # What tomorrow is worth, so the screen can say it instead of guessing.
    next_reward: Optional[int] = None


@router.get("/plans", response_model=list[Plan])
async def list_plans(db: AsyncSession = Depends(get_db_session)) -> list[Plan]:
    """Available tariffs. Public: the paywall is shown before sign-in."""
    rows = await plans_repo.list_active(db)
    return [
        Plan(
            id=row.id,
            product_id=row.product_id,
            title=row.title,
            billing_period=row.billing_period,
            price_usd_cents=row.price_usd,
            weekly_quota=row.weekly_quota,
        )
        for row in rows
    ]


@router.post("/rewards/daily", response_model=RewardResult)
async def claim_daily(
    ctx: Context = Depends(required_context),
    db: AsyncSession = Depends(get_db_session),
) -> RewardResult:
    """Claim today's reward.

    Safe to call on every launch: a second call on the same day grants nothing
    and does not break the streak, so the client does not need to track dates.
    """
    user, _ = ctx
    balance, granted = await wallet_repo.claim_daily_reward(db, user.id)
    wallet = await wallet_repo.ensure(db, user.id)

    schedule = settings.daily_reward_list
    next_index = wallet.reward_streak % len(schedule) if schedule else 0
    return RewardResult(
        granted=granted,
        streak=wallet.reward_streak,
        balance=balance.total,
        free_balance=balance.free,
        sub_balance=balance.sub,
        next_reward=schedule[next_index] if schedule else None,
    )
