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
from app.deps import Context, optional_context
from app.models.chat import ChatSummary
from app.models.payment import Balance
from app.models.tile import Category, Question, Tile
from app.models.user import PublicUser
from app.services import chat_service, tiles_data, wallet

router = APIRouter(prefix="/api/app", tags=["app"])


class AppConfig(BaseModel):
    image_teki_cost: int
    video_teki_cost: int
    push_enabled: bool
    google_enabled: bool
    apple_enabled: bool


class BootstrapResponse(BaseModel):
    user: Optional[PublicUser]
    balance: Optional[Balance]
    categories: list[Category]
    featured: list[Tile]
    freeform_question: Question
    # Chat sidebar (GET /api/chats gives the same list — included here too so
    # a cold-start app has everything, including chat history, in one call).
    chats: list[ChatSummary]
    config: AppConfig


@router.get("/bootstrap", response_model=BootstrapResponse)
async def bootstrap(ctx: Optional[Context] = Depends(optional_context)) -> BootstrapResponse:
    user: Optional[PublicUser] = None
    balance: Optional[Balance] = None
    chats: list[ChatSummary] = []
    if ctx is not None:
        u, session = ctx
        user = PublicUser.from_user(u)
        balance = await wallet.get_balance(u, session)
        chats = await chat_service.list_summaries(u.id)

    return BootstrapResponse(
        user=user,
        balance=balance,
        categories=tiles_data.get_categories(),
        featured=tiles_data.get_featured(),
        freeform_question=tiles_data.FREEFORM_STYLE_QUESTION,
        chats=chats,
        config=AppConfig(
            image_teki_cost=settings.image_teki_cost,
            video_teki_cost=settings.video_teki_cost,
            push_enabled=settings.push_enabled,
            google_enabled=settings.google_enabled,
            apple_enabled=settings.apple_enabled,
        ),
    )
