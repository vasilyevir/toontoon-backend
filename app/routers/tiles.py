"""Tile catalog + balance endpoints (read-only product data)."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session as get_db_session
from app.deps import Context, required_context
from app.models.payment import Balance
from app.models.tile import Category, Question, Tile
from app.services import tiles_data, wallet

router = APIRouter(prefix="/api", tags=["tiles"])


@router.get("/tiles", response_model=list[Category])
async def list_tiles() -> list[Category]:
    """Full catalog: 4 categories with all 31 tiles and their questions."""
    return tiles_data.get_categories()


@router.get("/tiles/featured", response_model=list[Tile])
async def featured_tiles() -> list[Tile]:
    """The 6 featured tiles shown first in the chat."""
    return tiles_data.get_featured()


@router.get("/tiles/freeform-question", response_model=Question)
async def freeform_question() -> Question:
    """The single style question asked for free-form prompts."""
    return tiles_data.FREEFORM_STYLE_QUESTION


@router.get("/balance", response_model=Balance)
async def balance(
    ctx: Context = Depends(required_context),
    db: AsyncSession = Depends(get_db_session),
) -> Balance:
    user, _ = ctx
    return await wallet.get_balance(db, user.id)
