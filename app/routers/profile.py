"""Profile management — update name, delete account."""
from __future__ import annotations

from fastapi import APIRouter, Cookie, Depends, Response

from app.config import settings
from app.cookies import clear_session_cookie
from app.deps import Context, required_context
from app.models.user import ProfileUpdate, PublicUser
from app.services import auth_service

router = APIRouter(prefix="/api/auth", tags=["profile"])


@router.patch("/profile")
async def update_profile(body: ProfileUpdate, ctx: Context = Depends(required_context)) -> PublicUser:
    user, _ = ctx
    user.name = body.name
    await auth_service.save_user(user)
    return PublicUser.from_user(user)


@router.delete("/profile")
async def delete_profile(
    response: Response,
    ctx: Context = Depends(required_context),
    session_cookie: str | None = Cookie(default=None, alias=settings.session_cookie_name),
):
    user, session = ctx
    await auth_service.delete_session(session.sid)
    await auth_service.delete_user(user.id)
    clear_session_cookie(response)
    return {"ok": True}
