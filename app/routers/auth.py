"""Authentication — magic link (v1) and Boostify OAuth (v2)."""
from __future__ import annotations

import time
from typing import Optional

from fastapi import APIRouter, Cookie, Depends, Query, Response
from fastapi.responses import RedirectResponse

from app.config import settings
from app.cookies import clear_session_cookie, set_session_cookie
from app.core.security import new_token
from app.deps import Context, optional_context
from app.models.user import MagicLinkRequest, PublicUser
from app.redis_client import get_client
from app.services import auth_service, boostify

router = APIRouter(prefix="/api/auth", tags=["auth"])


# ─── v1: Magic link ───────────────────────────────────────────────────────────


@router.post("/magic-link")
async def magic_link(body: MagicLinkRequest):
    """Issue a magic-link token.

    Email is NOT actually sent — the dev link is returned directly in the JSON
    response (temporary, until real email delivery is wired up).
    """
    token = await auth_service.create_magic_token(str(body.email))
    return {"ok": True, "devLink": f"/api/auth/verify?token={token}"}


@router.get("/verify")
async def verify(token: str = Query(...)):
    """Consume a magic-link token, create the session cookie, redirect to app."""
    email = await auth_service.consume_magic_token(token)
    if not email:
        return RedirectResponse(url=f"{settings.frontend_url}/?error=expired", status_code=302)

    user = await auth_service.get_or_create_magic_user(email)
    session = await auth_service.create_session(user)

    response = RedirectResponse(url=f"{settings.frontend_url}{settings.auth_success_redirect}", status_code=302)
    set_session_cookie(response, session.sid)
    return response


# ─── v2: Boostify OAuth ─────────────────────────────────────────────────────


@router.get("/boostify/login")
async def boostify_login():
    """Kick off the Boostify OAuth flow."""
    state = new_token()
    redis = get_client()
    await redis.set(f"oauth_state:{state}", "1", ex=600)
    return RedirectResponse(url=boostify.authorize_url(state), status_code=302)


@router.get("/boostify/callback")
async def boostify_callback(code: str = Query(...), state: str = Query(...)):
    redis = get_client()
    valid = await redis.get(f"oauth_state:{state}")
    if not valid:
        return RedirectResponse(url=f"{settings.frontend_url}/?error=oauth_state", status_code=302)
    await redis.delete(f"oauth_state:{state}")

    tokens = await boostify.exchange_code(code)
    user = await auth_service.get_or_create_boostify_user(tokens["user"])
    session = await auth_service.create_session(
        user,
        boostify_access_token=tokens["access_token"],
        boostify_refresh_token=tokens.get("refresh_token"),
        boostify_access_expires_at=time.time() + tokens.get("expires_in", 3600),
    )

    response = RedirectResponse(url=f"{settings.frontend_url}{settings.auth_success_redirect}", status_code=302)
    set_session_cookie(response, session.sid)
    return response


# ─── Session info / logout ──────────────────────────────────────────────────


@router.get("/me")
async def me(ctx: Optional[Context] = Depends(optional_context)) -> Optional[PublicUser]:
    """Return the current user, or ``null`` when not authenticated.

    Returns 200 with a null body (not 401) so the frontend can branch on it.
    """
    if ctx is None:
        return None
    user, _ = ctx
    return PublicUser.from_user(user)


@router.delete("/me")
async def logout(
    response: Response,
    session_cookie: Optional[str] = Cookie(default=None, alias=settings.session_cookie_name),
):
    if session_cookie:
        await auth_service.delete_session(session_cookie)
    clear_session_cookie(response)
    return {"ok": True}
