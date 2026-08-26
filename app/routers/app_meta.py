"""App-level bootstrap for native clients.

One round trip instead of several on cold start (``/auth/me`` + ``/balance``
+ ``/tiles`` + ``/tiles/featured`` + ``/tiles/freeform-question``). Works
signed-out too (``user``/``balance`` come back ``null``) so the app can
render the tile catalog before login.
"""
from __future__ import annotations

from datetime import timedelta
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.config import settings
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session as get_db_session
from app.deps import Context, optional_context
from app.models.payment import Balance
from app.models.user import PublicUser
from app.db.repositories import chat as chat_repo
from app.db.repositories import generations as generations_repo
from app.db.repositories import styles as styles_repo
from app.services import wallet

router = APIRouter(prefix="/api/app", tags=["app"])


class AppConfig(BaseModel):
    image_toontoon_cost: int
    video_toontoon_cost: int
    push_enabled: bool
    google_enabled: bool
    apple_enabled: bool
    video_enabled: bool


class BootstrapResponse(BaseModel):
    user: Optional[PublicUser]
    balance: Optional[Balance]
    # Каталог: витрина главной и набор дня. Плитки убраны вместе с роутером —
    # каталог теперь стили по шести направлениям (CH-14). Пока стили не написаны
    # под выбранную модель, оба списка пусты, и это честнее заглушек.
    home_styles: list[dict]
    shots: list[dict]
    # The tail of the single chat thread (CH-20). A cold start gets enough to
    # draw the screen; older messages are paged in from /api/chat/messages.
    messages: list[dict]
    # Заказы, которые сейчас в пути. Обычно пусто; непусто ровно тогда, когда
    # человек закрыл приложение, не дождавшись кадра, — и без этого списка
    # вернувшемуся нечем показать, что работа идёт.
    pending: list[dict]
    config: AppConfig


def _pending(row) -> dict:
    """Заказ в пути — тем же именами полей, что и готовая работа.

    Приложение читает это в тот же тип, что и всё остальное из истории:
    отдельная форма ради двух полей означала бы второй разбор и второй способ
    ошибиться.
    """
    return {
        "id": row.id,
        "type": (row.request_params or {}).get("type", "image"),
        "operation": row.operation,
        "status": row.status,
        "prompt": row.prompt,
        "result_url": None,
        "thumbnail_url": None,
        "cost": row.cost,
        "style_id": row.style_id,
        "share_id": None,
        "created_at": row.created_at.isoformat(),
    }


def _style(row) -> dict:
    """Стиль в том же виде, что и в /api/styles — один формат на всё приложение."""
    return {
        "id": row.id,
        "title": row.title,
        "description": row.description,
        "category": row.category,
        "operation": row.operation,
        "input_spec": row.input_spec or {},
        "cost": row.cost,
    }


@router.get("/bootstrap", response_model=BootstrapResponse)
async def bootstrap(
    ctx: Optional[Context] = Depends(optional_context),
    db: AsyncSession = Depends(get_db_session),
) -> BootstrapResponse:
    user: Optional[PublicUser] = None
    balance: Optional[Balance] = None
    messages: list[dict] = []
    pending: list[dict] = []
    if ctx is not None:
        u, session = ctx
        user = PublicUser.from_row(u, provider=session.provider)
        balance = await wallet.get_balance(db, u.id)
        rows = await chat_repo.list_messages(db, u, limit=20)
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
        pending = [
            _pending(r)
            for r in await generations_repo.pending_for_user(
                db, u.id,
                younger_than=timedelta(minutes=settings.stale_generation_minutes),
            )
        ]

    from datetime import datetime, timezone

    home = await styles_repo.list_styles(db, home_only=True, limit=8)
    shots = await styles_repo.daily_shots(db, datetime.now(timezone.utc).date())

    return BootstrapResponse(
        user=user,
        balance=balance,
        home_styles=[_style(s) for s in home],
        shots=[_style(s) for s in shots],
        messages=messages,
        pending=pending,
        config=AppConfig(
            image_toontoon_cost=settings.image_toontoon_cost,
            video_toontoon_cost=settings.video_toontoon_cost,
            push_enabled=settings.push_enabled,
            google_enabled=settings.google_enabled,
            apple_enabled=settings.apple_enabled,
            video_enabled=settings.video_enabled,
        ),
    )
