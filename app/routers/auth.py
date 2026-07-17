"""Authentication — magic link (v1) and Boostyfi OAuth v2 (PKCE)."""
from __future__ import annotations

import time
from typing import Optional

from fastapi import APIRouter, Cookie, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import RedirectResponse

from app.config import settings
from app.cookies import clear_session_cookie, set_session_cookie
from app.core import rate_limit
from app.core.security import hash_password, new_token, verify_password
from app.deps import Context, optional_context
from app.models.user import (
    AuthProvider,
    AuthResult,
    ForgotPasswordRequest,
    LoginRequest,
    MagicLinkRequest,
    PublicUser,
    RegisterRequest,
    ResetPasswordRequest,
)
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


# ─── Email + password (v1.5) ────────────────────────────────────────────────


def _client_ip(request: Request) -> str:
    # nginx sets X-Real-IP / X-Forwarded-For; fall back to the socket peer.
    return (
        request.headers.get("x-real-ip")
        or (request.headers.get("x-forwarded-for", "").split(",")[0].strip())
        or (request.client.host if request.client else "unknown")
    )


@router.post("/register", response_model=AuthResult, status_code=status.HTTP_201_CREATED)
async def register(body: RegisterRequest, request: Request, response: Response) -> AuthResult:
    """Create an email+password account and start a session.

    Registration is one-per-email. The session token is returned in the JSON
    body (for native apps) and also set as the session cookie (for the web).
    """
    email = str(body.email).strip().lower()

    # Rate limit account creation per IP (anti-abuse).
    allowed, _ = await rate_limit.hit(f"register:ip:{_client_ip(request)}", 10, 3600)
    if not allowed:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, detail="Too many attempts, try later")

    if await auth_service.get_user_by_email(email):
        raise HTTPException(status.HTTP_409_CONFLICT, detail="This email is already registered")

    user = await auth_service.create_email_user(
        email=email, password_hash=hash_password(body.password), name=body.name.strip()
    )
    session = await auth_service.create_session(user)
    set_session_cookie(response, session.sid)
    return AuthResult(user=PublicUser.from_user(user), session_token=session.sid)


@router.post("/login", response_model=AuthResult)
async def login(body: LoginRequest, request: Request, response: Response) -> AuthResult:
    """Log in with email + password. Returns the session token (JSON) + cookie."""
    email = str(body.email).strip().lower()

    # Brute-force protection: cap attempts per email and per IP.
    ip = _client_ip(request)
    ok_email, _ = await rate_limit.hit(f"login:email:{email}", 8, 900)
    ok_ip, _ = await rate_limit.hit(f"login:ip:{ip}", 20, 900)
    if not (ok_email and ok_ip):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, detail="Too many attempts, try later")

    user = await auth_service.get_user_by_email(email)
    # Single generic error — never reveal whether the email exists.
    if not user or not user.password_hash or not verify_password(body.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")

    session = await auth_service.create_session(user)
    set_session_cookie(response, session.sid)
    return AuthResult(user=PublicUser.from_user(user), session_token=session.sid)


@router.post("/forgot-password")
async def forgot_password(body: ForgotPasswordRequest, request: Request):
    """Start a password reset. ALWAYS returns 200 (never reveals whether the
    email exists). A single-use reset token (TTL 1h) is stored in Redis.

    Email delivery is not wired yet, so — exactly like the magic-link flow — the
    token is returned in the JSON response for now. TODO(email): once an ESP is
    configured, send the token by email and STOP returning `devToken`/`devLink`.
    """
    email = str(body.email).strip().lower()

    ip = _client_ip(request)
    ok_email, _ = await rate_limit.hit(f"forgot:email:{email}", 3, 900)
    ok_ip, _ = await rate_limit.hit(f"forgot:ip:{ip}", 10, 3600)
    if not (ok_email and ok_ip):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, detail="Too many attempts, try later")

    user = await auth_service.get_user_by_email(email)
    result: dict = {"ok": True}
    # Only email+password accounts have a password to reset.
    if user and user.provider == AuthProvider.EMAIL and user.password_hash:
        token = await auth_service.create_reset_token(email)
        result["devToken"] = token
        result["devLink"] = f"{settings.frontend_url}/reset-password?token={token}"
    return result


@router.post("/reset-password")
async def reset_password(body: ResetPasswordRequest, request: Request):
    """Consume a reset token and set a new password (Argon2)."""
    ok_ip, _ = await rate_limit.hit(f"reset:ip:{_client_ip(request)}", 20, 900)
    if not ok_ip:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, detail="Too many attempts, try later")

    email = await auth_service.consume_reset_token(body.token)
    if not email:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="invalid_or_expired")

    user = await auth_service.get_user_by_email(email)
    if not user:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="invalid_or_expired")

    user.password_hash = hash_password(body.new_password)
    await auth_service.save_user(user)
    return {"ok": True}


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
