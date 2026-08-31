"""Profile management — update name and avatar, delete account."""
from __future__ import annotations

from fastapi import APIRouter, Cookie, Depends, Response
from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.cookies import clear_session_cookie
from app.db.repositories import media as media_repo
from app.db.repositories import users as users_repo
from app.db.session import get_session as get_db_session
from app.deps import Context, required_context
from app.models.user import ProfileUpdate, PublicUser
from app.services import auth_service

router = APIRouter(prefix="/api/auth", tags=["profile"])


@router.patch("/profile")
async def update_profile(
    body: ProfileUpdate,
    ctx: Context = Depends(required_context),
    db: AsyncSession = Depends(get_db_session),
) -> PublicUser:
    user, session = ctx
    row = await users_repo.get(db, user.id)
    if row is None:
        return PublicUser.from_row(user, provider=session.provider)
    if body.name is not None:
        row.name = body.name
    if body.avatar is not None:
        # The column holds an object key, never a URL — that is what keeps a
        # storage or CDN change from becoming a data migration.
        row.avatar_key = body.avatar
    await db.flush()
    return PublicUser.from_row(row, provider=session.provider)


@router.delete("/profile")
async def delete_profile(
    response: Response,
    ctx: Context = Depends(required_context),
    db: AsyncSession = Depends(get_db_session),
    session_cookie: str | None = Cookie(default=None, alias=settings.session_cookie_name),
):
    """Delete the account.

    This is **anonymisation, not a DELETE**: the ledger references the user by
    foreign key and the database rightly refuses to erase a row that money
    history points at. So the person disappears — email, name and avatar are
    cleared, the session is killed — while the bookkeeping stays intact (CH-15).

    Файлы стираются здесь же. До 31 августа 2026 не стирались: строка
    обезличивалась, а снимки оставались лежать в хранилище. Человек нажимал
    «удалить», ему отвечали «готово», и его лицо продолжало храниться у нас.
    """
    user, session = ctx
    # Сперва файлы, потом обезличивание. Обратный порядок опаснее: если стирание
    # упадёт на середине, у нас останется безымянная строка и чьи-то снимки,
    # которые уже не привязать к человеку и не найти по запросу.
    стёрто = await media_repo.erase_everything_of(db, user.id)
    row = await users_repo.get(db, user.id)
    if row is not None:
        row.email = None
        row.name = None
        row.avatar_key = None
        row.password_hash = None
        row.deleted_at = func.now()
        await db.flush()
    await auth_service.delete_session(session.sid)
    clear_session_cookie(response)
    return {"ok": True, "files_erased": стёрто}
