"""Authentication — magic link (v1) and Boostyfi OAuth v2 (PKCE)."""
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


# ─── v2: Boostyfi OAuth (PKCE / S256, required) ─────────────────────────────


@router.get("/boostify/login")
async def boostify_login():
    """Kick off the Boostyfi OAuth flow with PKCE.

    Generates a PKCE pair and a CSRF state token, stores the verifier in Redis
    keyed by state (TTL 10 min), then redirects the browser to Boostyfi.
    The verifier is never sent to the browser.
    """
    state = new_token()
    code_verifier, code_challenge = boostify.pkce_pair()

    redis = get_client()
    # Store verifier alongside the state so the callback can retrieve it.
    await redis.set(f"oauth_state:{state}", code_verifier, ex=600)

    return RedirectResponse(
        url=boostify.authorize_url(state, code_challenge),
        status_code=302,
    )


@router.get("/boostify/callback")
async def boostify_callback(code: str = Query(...), state: str = Query(...)):
    """Complete the OAuth flow: verify state, exchange code (with PKCE verifier)."""
    redis = get_client()
    code_verifier = await redis.get(f"oauth_state:{state}")
    if not code_verifier:
        return RedirectResponse(url=f"{settings.frontend_url}/?error=oauth_state", status_code=302)
    await redis.delete(f"oauth_state:{state}")

    # code_verifier may come back as bytes from Redis.
    if isinstance(code_verifier, bytes):
        code_verifier = code_verifier.decode()

    tokens = await boostify.exchange_code(code, code_verifier)
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
