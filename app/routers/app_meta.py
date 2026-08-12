"""App-level bootstrap for native clients.

One round trip instead of several on cold start (``/auth/me`` + ``/balance``
+ ``/tiles`` + ``/tiles/featured`` + ``/tiles/freeform-question``). Works
signed-out too (``user``/``balance`` come back ``null``) so the app can
render the tile catalog before login.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.config import settings
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session as get_db_session
from app.deps import Context, optional_context
from app.models.payment import Balance
from app.models.tile import Category, Question, Tile
from app.models.user import PublicUser
from app.db.repositories import chat as chat_repo
from app.services import tiles_data, wallet

router = APIRouter(prefix="/api/app", tags=["app"])


class AppConfig(BaseModel):
    image_teki_cost: int
    video_teki_cost: int
    push_enabled: bool
    google_enabled: bool
    apple_enabled: bool
    video_enabled: bool


class BootstrapResponse(BaseModel):
    user: Optional[PublicUser]
    balance: Optional[Balance]
    categories: list[Category]
    featured: list[Tile]
    freeform_question: Question
    # The tail of the single chat thread (CH-20). A cold start gets enough to
    # draw the screen; older messages are paged in from /api/chat/messages.
    messages: list[dict]
    config: AppConfig


@router.get("/bootstrap", response_model=BootstrapResponse)
async def bootstrap(
    ctx: Optional[Context] = Depends(optional_context),
    db: AsyncSession = Depends(get_db_session),
) -> BootstrapResponse:
    user: Optional[PublicUser] = None
    balance: Optional[Balance] = None
    messages: list[dict] = []
    if ctx is not None:
        u, session = ctx
        user = PublicUser.from_row(u, provider=session.provider)
        balance = await wallet.get_balance(db, u.id)
        rows = await chat_repo.list_messages(db, u.id, limit=20)
        messages = [
            {
                "id": r.id,
                "role": r.role,
                "content": r.content,
                "generation_id": r.generation_id,
                "created_at": r.created_at.isoformat(),
            }
            for r in rows
        ]

    return BootstrapResponse(
        user=user,
        balance=balance,
        categories=tiles_data.get_categories(),
        featured=tiles_data.get_featured(),
        freeform_question=tiles_data.FREEFORM_STYLE_QUESTION,
        messages=messages,
        config=AppConfig(
            image_teki_cost=settings.image_teki_cost,
            video_teki_cost=settings.video_teki_cost,
            push_enabled=settings.push_enabled,
            google_enabled=settings.google_enabled,
            apple_enabled=settings.apple_enabled,
            video_enabled=settings.video_enabled,
        ),
    )
