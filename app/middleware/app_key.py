"""App-key + HMAC request-signing middleware for the mobile client.

Canonical string (MUST byte-match the iOS client's APISecurity.decorate):

    METHOD\nPATH\nTIMESTAMP\nsha256_hex(body)

  * METHOD    — uppercase HTTP method (e.g. POST)
  * PATH      — request path incl. the /api prefix, no query (e.g. /api/auth/login)
  * TIMESTAMP — unix seconds as a string (X-Toontoon-Timestamp)
  * body      — raw request body (empty for GET; empty for multipart uploads,
                which is why /api/uploads is exempt)

Signature = HMAC-SHA256(key=APP_SECRET, msg=canonical), lowercase hex, sent as
X-Toontoon-Signature. The app-key is echoed in X-Toontoon-App-Key.

Behaviour is gated by settings.app_key_required:
  * false (default) — pass everything through untouched (zero prod impact).
  * true            — reject unsigned/mismatched /api/* requests with 401,
                      except settings.app_key_exempt_prefixes.
"""
from __future__ import annotations

import hashlib
import hmac
import time

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.config import settings


class AppKeyMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        # Only guard HTTP, only when enabled, only signed API paths.
        if scope["type"] != "http" or not settings.app_key_required:
            await self.app(scope, receive, send)
            return

        path: str = scope["path"]
        if not path.startswith("/api/") or self._exempt(path):
            await self.app(scope, receive, send)
            return

        # Buffer the full request body so we can hash it, then replay it downstream.
        body = b""
        while True:
            message = await receive()
            if message["type"] != "http.request":
                # Not a normal request body (e.g. disconnect) — let the app handle it.
                await self._passthrough(scope, message, receive, send)
                return
            body += message.get("body", b"")
            if not message.get("more_body", False):
                break

        error = self._verify(scope, path, body)
        if error is not None:
            await JSONResponse({"detail": error}, status_code=401)(scope, receive, send)
            return

        replayed = {"done": False}

        async def replay() -> Message:
            if not replayed["done"]:
                replayed["done"] = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        await self.app(scope, replay, send)

    def _exempt(self, path: str) -> bool:
        return any(path.startswith(prefix) for prefix in settings.app_key_exempt_list)

    def _verify(self, scope: Scope, path: str, body: bytes) -> str | None:
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
        app_key = headers.get("x-toontoon-app-key")
        ts = headers.get("x-toontoon-timestamp")
        sig = headers.get("x-toontoon-signature")

        if not app_key or not hmac.compare_digest(app_key, settings.app_key):
            return "app key required"
        if not ts or not sig:
            return "request signature required"
        try:
            skew = abs(time.time() - int(ts))
        except ValueError:
            return "invalid timestamp"
        if skew > settings.app_sig_max_skew_seconds:
            return "stale request signature"

        body_hash = hashlib.sha256(body).hexdigest()
        canonical = f"{scope['method']}\n{path}\n{ts}\n{body_hash}"
        expected = hmac.new(settings.app_secret.encode(), canonical.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, sig):
            return "invalid request signature"
        return None

    async def _passthrough(self, scope: Scope, first: Message, receive: Receive, send: Send) -> None:
        sent = {"done": False}

        async def replay() -> Message:
            if not sent["done"]:
                sent["done"] = True
                return first
            return await receive()

        await self.app(scope, replay, send)
