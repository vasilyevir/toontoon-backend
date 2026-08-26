"""TOONTOON backend — FastAPI application entrypoint."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import db, storage
from app.config import settings
from app.middleware.app_key import AppKeyMiddleware
from app.redis_client import connect, disconnect
from app.routers import (
    favorites,
    guided,
    ideas,
    profiles,
    app_meta,
    auth,
    billing,
    chat,
    media,
    onboarding,
    styles,
    events,
    generate,
    generations,
    payments,
    profile,
    push,
    webhooks,
)

UPLOAD_DIR = Path("uploads")


def warn_about_debug_flags() -> None:
    """Прокричать про отладочные флаги, оставленные включёнными в проде.

    Не падаем — падение посреди ночи хуже, — но молчать нельзя: каждый из этих
    флагов по отдельности отдаёт чужой аккаунт.

    Отдельной функцией, а не строками внутри `lifespan`, чтобы это можно было
    вызвать из теста. Проверка, которую тест повторяет своими словами вместо
    того, чтобы вызвать, продолжает проходить и после того, как её удалили
    отсюда.
    """
    log = logging.getLogger("toontoon")
    if settings.accept_storekit_test_root and not settings.debug:
        # Сертификат локального StoreKit лежит внутри Xcode у всех, и с ним
        # подписку себе выпишет кто угодно.
        log.error(
            "ACCEPT_STOREKIT_TEST_ROOT=true при DEBUG=false: чеки, подписанные "
            "тестовым корнем Xcode, принимаются как настоящие. Выключите его."
        )
    if settings.expose_dev_tokens and not settings.debug:
        # С этим флагом чужой пароль меняется одним запросом.
        log.error(
            "EXPOSE_DEV_TOKENS=true при DEBUG=false: токены сброса пароля уходят "
            "в ответе API. Выключите его до публикации."
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    UPLOAD_DIR.mkdir(exist_ok=True)
    await connect()
    # Postgres is being introduced alongside Redis; nothing reads from it yet,
    # so an unreachable database must not stop local work. Once the first router
    # depends on it this becomes a hard failure — that is the point of migrating.
    try:
        await db.connect()
    except Exception as exc:  # noqa: BLE001 — startup diagnostics
        logging.getLogger("toontoon.db").error(
            "PostgreSQL unavailable (%s). Continuing: no router reads from it yet. "
            "Start it with: docker start toontoon-postgres",
            exc,
        )
    try:
        await storage.startup()
    except Exception as exc:  # noqa: BLE001 — startup diagnostics
        logging.getLogger("toontoon.storage").error(
            "Object storage unavailable (%s). Start it with: docker start toontoon-minio", exc
        )
    warn_about_debug_flags()

    yield
    await db.disconnect()
    await disconnect()


app = FastAPI(
    title=settings.app_name,
    version="1.0.0",
    description="Identity, wallet and AI content generation for TOONTOON.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mobile app-key + HMAC verification. No-op unless settings.app_key_required.
app.add_middleware(AppKeyMiddleware)

# The public /uploads mount is gone: it served every user's reference photos —
# faces included — to anyone with a link. Media now goes through /api/media,
# which checks who is asking. The directory itself stays as scratch space for
# the video pipeline until that moves to storage too.

app.include_router(auth.router)
app.include_router(app_meta.router)
app.include_router(media.router)
app.include_router(billing.router)
app.include_router(webhooks.router)
app.include_router(onboarding.router)
app.include_router(styles.router)
app.include_router(favorites.router)
app.include_router(guided.router)
app.include_router(ideas.router)
app.include_router(profiles.router)
app.include_router(profile.router)
app.include_router(chat.router)
app.include_router(generate.router)
app.include_router(generations.router)
app.include_router(payments.router)
app.include_router(push.router)
app.include_router(events.router)


@app.get("/health", tags=["meta"])
async def health() -> dict:
    return {"status": "ok", "app": settings.app_name}
