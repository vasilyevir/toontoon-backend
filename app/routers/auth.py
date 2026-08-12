"""Authentication: guest, magic link, email+password, Apple and Google.

Identity lives in PostgreSQL; sessions stay in Redis (TTL is what Redis is for).
Boostyfi is gone from the mobile product — purchases go through the App Store
(CH-17), so its OAuth and wallet grant had no user left.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Query, Request, Response, status
from fastapi.responses import RedirectResponse

from app.config import settings
from app.cookies import clear_session_cookie, set_session_cookie
from app.core import rate_limit
from app.core.security import hash_password, new_token, verify_password
from app.deps import Context, optional_context
from app.models.user import (
    AppleAuthRequest,
    AuthProvider,
    AuthResult,
    ForgotPasswordRequest,
    LoginRequest,
    MagicLinkRequest,
    PublicUser,
    RegisterRequest,
    ResetPasswordRequest,
)
from app.db.repositories import users as users_repo
from app.db.repositories import wallet as wallet_repo
from app.db.session import get_session as get_db_session
from app.redis_client import get_client
from app.services import apple_oauth, auth_service, google_oauth, identity_service
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(prefix="/api/auth", tags=["auth"])


# ─── Guest ────────────────────────────────────────────────────────────────────


@router.post("/guest", response_model=AuthResult, status_code=status.HTTP_201_CREATED)
async def create_guest(
    response: Response,
    db: AsyncSession = Depends(get_db_session),
) -> AuthResult:
    """Start an anonymous session on first launch.

    The app calls this once and keeps the token in the Keychain. From here on a
    guest is an ordinary user: they have a balance, their generations are stored
    and their chat is kept — signing in later merges all of it into the account
    instead of asking them to start over (CH-16).

    Unlike every other identity in this file, a guest lives in PostgreSQL: this
    is the first route on the new storage.
    """
    user = await users_repo.create_guest(db)
    await wallet_repo.grant(
        db,
        user.id,
        amount=settings.signup_teki_balance,
        bucket="free",
        reason="signup",
        idempotency_key=f"signup:{user.id}",
    )
    session = await auth_service.create_session_for_user_id(user.id, AuthProvider.GUEST)
    set_session_cookie(response, session.sid)
    return AuthResult(
        user=PublicUser(
            id=user.id,
            provider=AuthProvider.GUEST,
            email=None,
            name=None,
            avatar=None,
            created_at=user.created_at,
        ),
        session_token=session.sid,
    )


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
async def verify(token: str = Query(...), db: AsyncSession = Depends(get_db_session)):
    """Consume a magic-link token, create the session cookie, redirect to app.

    Web-only (redirect-based). Native clients should use
    ``GET /api/auth/magic-link/verify`` instead — same token, JSON response.
    """
    email = await auth_service.consume_magic_token(token)
    if not email:
        return RedirectResponse(url=f"{settings.frontend_url}/?error=expired", status_code=302)

    user = await identity_service.get_or_create_oauth_user(
        db, provider="email", external_id=email, email=email
    )
    session = await auth_service.create_session_for_user_id(user.id, AuthProvider.MAGIC)

    response = RedirectResponse(url=f"{settings.frontend_url}{settings.auth_success_redirect}", status_code=302)
    set_session_cookie(response, session.sid)
    return response


@router.get("/magic-link/verify", response_model=AuthResult)
async def magic_link_verify_json(
    token: str = Query(...), *, response: Response, db: AsyncSession = Depends(get_db_session)
) -> AuthResult:
    """Same magic-link token as ``/verify``, but for native/app clients:
    returns ``{user, session_token}`` directly in JSON instead of a redirect
    with ``Set-Cookie`` — no cookie-jar interception needed. The cookie is
    still set too (harmless if the caller ignores it), so this also works
    fine from a browser if ever needed.
    """
    email = await auth_service.consume_magic_token(token)
    if not email:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="invalid_or_expired")

    user = await identity_service.get_or_create_oauth_user(
        db, provider="email", external_id=email, email=email
    )
    session = await auth_service.create_session_for_user_id(user.id, AuthProvider.MAGIC)
    set_session_cookie(response, session.sid)
    return AuthResult(
        user=PublicUser.from_row(user, provider=AuthProvider.MAGIC), session_token=session.sid
    )


# ─── Email + password (v1.5) ────────────────────────────────────────────────


def _client_ip(request: Request) -> str:
    # nginx sets X-Real-IP / X-Forwarded-For; fall back to the socket peer.
    return (
        request.headers.get("x-real-ip")
        or (request.headers.get("x-forwarded-for", "").split(",")[0].strip())
        or (request.client.host if request.client else "unknown")
    )


@router.post("/register", response_model=AuthResult, status_code=status.HTTP_201_CREATED)
async def register(
    body: RegisterRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db_session),
) -> AuthResult:
    """Create an email+password account and start a session.

    Registration is one-per-email. The session token is returned in the JSON
    body (for native apps) and also set as the session cookie (for the web).
    """
    email = str(body.email).strip().lower()

    # Rate limit account creation per IP (anti-abuse).
    allowed, _ = await rate_limit.hit(f"register:ip:{_client_ip(request)}", 10, 3600)
    if not allowed:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, detail="Too many attempts, try later")

    if await users_repo.get_by_email(db, email):
        raise HTTPException(status.HTTP_409_CONFLICT, detail="This email is already registered")

    user = await identity_service.create_email_user(
        db, email=email, password_hash=hash_password(body.password), name=body.name.strip()
    )
    session = await auth_service.create_session_for_user_id(user.id, AuthProvider.EMAIL)
    set_session_cookie(response, session.sid)
    return AuthResult(
        user=PublicUser.from_row(user, provider=AuthProvider.EMAIL), session_token=session.sid
    )


@router.post("/login", response_model=AuthResult)
async def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db_session),
) -> AuthResult:
    """Log in with email + password. Returns the session token (JSON) + cookie."""
    email = str(body.email).strip().lower()

    # Brute-force protection: cap attempts per email and per IP.
    ip = _client_ip(request)
    ok_email, _ = await rate_limit.hit(f"login:email:{email}", 8, 900)
    ok_ip, _ = await rate_limit.hit(f"login:ip:{ip}", 20, 900)
    if not (ok_email and ok_ip):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, detail="Too many attempts, try later")

    user = await users_repo.get_by_email(db, email)
    # Single generic error — never reveal whether the email exists.
    if not user or not user.password_hash or not verify_password(body.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")

    session = await auth_service.create_session_for_user_id(user.id, AuthProvider.EMAIL)
    set_session_cookie(response, session.sid)
    return AuthResult(
        user=PublicUser.from_row(user, provider=AuthProvider.EMAIL), session_token=session.sid
    )


@router.post("/forgot-password")
async def forgot_password(
    body: ForgotPasswordRequest,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
):
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

    user = await users_repo.get_by_email(db, email)
    result: dict = {"ok": True}
    # Only accounts that actually have a password can reset one.
    if user and user.password_hash:
        token = await auth_service.create_reset_token(email)
        result["devToken"] = token
        result["devLink"] = f"{settings.frontend_url}/reset-password?token={token}"
    return result


@router.post("/reset-password")
async def reset_password(
    body: ResetPasswordRequest,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
):
    """Consume a reset token and set a new password (Argon2)."""
    ok_ip, _ = await rate_limit.hit(f"reset:ip:{_client_ip(request)}", 20, 900)
    if not ok_ip:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, detail="Too many attempts, try later")

    email = await auth_service.consume_reset_token(body.token)
    if not email:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="invalid_or_expired")

    user = await users_repo.get_by_email(db, email)
    if not user:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="invalid_or_expired")

    user.password_hash = hash_password(body.new_password)
    await db.flush()
    return {"ok": True}


@router.get("/google/login")
async def google_login(platform: str = Query(default="web")):
    """Kick off Google OAuth.

    ``platform=app`` is used by the native app (opens this URL inside
    ``ASWebAuthenticationSession``): on success the callback redirects to a
    deep link (``{app_deep_link_scheme}://auth/callback?token=...``) instead
    of the web frontend, so the app can capture the session token straight
    from the URL — no cookie jar needed. Default ``platform=web`` behaves
    exactly like the Boostyfi flow (cookie + redirect to the frontend).
    """
    if not settings.google_enabled:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Google sign-in is not configured yet (missing GOOGLE_CLIENT_ID/SECRET)",
        )
    state = new_token()
    redis = get_client()
    await redis.set(f"oauth_state:google:{state}", platform, ex=600)
    return RedirectResponse(url=google_oauth.authorize_url(state), status_code=302)


@router.get("/google/callback")
async def google_callback(
    code: str = Query(...),
    state: str = Query(...),
    db: AsyncSession = Depends(get_db_session),
):
    redis = get_client()
    platform = await redis.get(f"oauth_state:google:{state}")
    if platform is None:
        return RedirectResponse(url=f"{settings.frontend_url}/login?error=oauth_state", status_code=302)
    await redis.delete(f"oauth_state:google:{state}")
    if isinstance(platform, bytes):
        platform = platform.decode()

    try:
        claims = await google_oauth.exchange_code(code)
    except Exception:
        error_target = (
            f"{settings.app_deep_link_scheme}://auth/callback?error=google"
            if platform == "app"
            else f"{settings.frontend_url}/login?error=google"
        )
        return RedirectResponse(url=error_target, status_code=302)

    user = await identity_service.get_or_create_oauth_user(
        db,
        provider="google",
        external_id=claims["sub"],
        email=claims.get("email"),
        name=claims.get("name"),
    )
    session = await auth_service.create_session_for_user_id(user.id, AuthProvider.GOOGLE)

    if platform == "app":
        return RedirectResponse(
            url=f"{settings.app_deep_link_scheme}://auth/callback?token={session.sid}",
            status_code=302,
        )

    response = RedirectResponse(url=f"{settings.frontend_url}{settings.auth_success_redirect}", status_code=302)
    set_session_cookie(response, session.sid)
    return response


# ─── Sign in with Apple (native app) ────────────────────────────────────────


@router.post("/oauth/apple", response_model=AuthResult)
async def apple_auth(
    body: AppleAuthRequest,
    response: Response,
    db: AsyncSession = Depends(get_db_session),
) -> AuthResult:
    """Verify an Apple ``identity_token`` (from ``ASAuthorizationAppleIDProvider``
    on the client) and start a session. Returns ``{user, session_token}`` in
    JSON (same shape as register/login) — no redirect involved, the app talks
    to Apple itself and only hands us the token to verify.
    """
    if not settings.apple_enabled:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Sign in with Apple is not configured yet (missing APPLE_BUNDLE_ID/APPLE_SERVICE_ID)",
        )
    try:
        claims = await apple_oauth.verify_identity_token(body.identity_token)
    except apple_oauth.InvalidAppleToken as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail=str(exc))

    user = await identity_service.get_or_create_oauth_user(
        db,
        provider="apple",
        external_id=claims["sub"],
        email=claims.get("email"),
        name=body.name,
    )
    session = await auth_service.create_session_for_user_id(user.id, AuthProvider.APPLE)
    set_session_cookie(response, session.sid)
    return AuthResult(
        user=PublicUser.from_row(user, provider=AuthProvider.APPLE), session_token=session.sid
    )


# ─── Session info / logout ──────────────────────────────────────────────────


@router.get("/me")
async def me(ctx: Optional[Context] = Depends(optional_context)) -> Optional[PublicUser]:
    """Return the current user, or ``null`` when not authenticated.

    Returns 200 with a null body (not 401) so the frontend can branch on it.
    """
    if ctx is None:
        return None
    user, session = ctx
    return PublicUser.from_row(user, provider=session.provider)


@router.delete("/me")
async def logout(
    response: Response,
    session_cookie: Optional[str] = Cookie(default=None, alias=settings.session_cookie_name),
    authorization: Optional[str] = Header(default=None),
):
    sid = session_cookie
    if not sid and authorization and authorization.lower().startswith("bearer "):
        sid = authorization[7:].strip()
    if sid:
        await auth_service.delete_session(sid)
    clear_session_cookie(response)
    return {"ok": True}
