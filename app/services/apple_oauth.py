"""Sign in with Apple — verifies client-issued identity tokens.

Unlike Google/Boostyfi (server-to-server code exchange, see
``google_oauth.py`` / ``boostify.py``), the app completes
``ASAuthorizationAppleIDProvider`` locally and POSTs us the resulting
``identity_token`` directly. That token crosses an untrusted boundary (the
client), so — unlike the other two providers — we MUST verify its signature
ourselves, against Apple's public JWKS, rather than just decoding the
payload.

No client secret is required for this: verification only needs Apple's
public keys (fetched live, cached) plus our own audience(s)
(``settings.apple_bundle_id`` / ``apple_service_id``) to check the ``aud``
claim.
"""
from __future__ import annotations

import jwt
from jwt import PyJWKClient

from app.config import settings

_JWKS_URL = "https://appleid.apple.com/auth/keys"
_ISSUER = "https://appleid.apple.com"

_jwk_client: PyJWKClient | None = None


def _client() -> PyJWKClient:
    global _jwk_client
    if _jwk_client is None:
        _jwk_client = PyJWKClient(_JWKS_URL)
    return _jwk_client


class InvalidAppleToken(Exception):
    pass


async def verify_identity_token(identity_token: str) -> dict:
    """Verify signature + standard claims. Returns ``{sub, email}``.

    Raises ``InvalidAppleToken`` on any failure (bad signature, wrong
    issuer/audience, expired, malformed).
    """
    audiences = settings.apple_audiences
    try:
        signing_key = _client().get_signing_key_from_jwt(identity_token)
        claims = jwt.decode(
            identity_token,
            signing_key.key,
            algorithms=["RS256"],
            audience=audiences or None,
            issuer=_ISSUER,
            options={"verify_aud": bool(audiences)},
        )
    except Exception as exc:  # noqa: BLE001 — collapse all jwt/network errors to one type
        raise InvalidAppleToken(f"invalid Apple identity token: {exc}") from exc

    sub = claims.get("sub")
    if not sub:
        raise InvalidAppleToken("Apple identity token missing sub claim")

    return {"sub": str(sub), "email": claims.get("email")}
