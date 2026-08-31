"""TOONTOON backend — FastAPI application entrypoint."""
from __future__ import annotations

import logging
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app import db, storage
from app.config import settings
from app.middleware.app_key import AppKeyMiddleware
from app.redis_client import connect, disconnect
from app.services import diagnosis, wallet
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


def warn_if_nobody_is_watching() -> None:
    """Сказать вслух, что за сервисом никто не следит.

    Предупреждением, а не ошибкой, и отдельно от опасных флагов: те отдают
    чужой аккаунт, а это — упущение. Кричать о нём тем же голосом значит
    приучить читать «ошибку» на старте как норму, и тогда настоящая потеряется
    среди привычных.

    Сказать всё же надо: без сторожа отказ находят по жалобе человека, а
    жалуется меньшинство — остальные просто удаляют приложение.
    """
    if settings.debug or settings.watchdog_token:
        return
    logging.getLogger("toontoon.watchdog").warning(
        "WATCHDOG_TOKEN не задан: /health/pulse выключен, и об отказах никто "
        "не узнает, пока не придёт посмотреть."
    )


async def settle_unpaid_refunds() -> None:
    """Вернуть деньги за работы, которым их не вернула фоновая задача.

    Именно на старте, и это не случайность: сюда попадает то, что случилось,
    когда возвращать было нечем — процесс убили посреди возврата, база легла.
    Следующий запуск и есть первый момент, когда доплатить снова возможно.

    Не роняет старт. Приложение, которое не поднимается из-за сверки, вредит
    больше, чем невозвращённые деньги: их вернёт следующий запуск, а лежащий
    сервис не сделает и этого.

    Реплик несколько, и сверку выполнит каждая. Это безвредно: проводка
    идемпотентна по ключу платежа, и вторая реплика просто ничего не найдёт.
    """
    try:
        async with db.session_scope() as session:
            await wallet.settle_owed(session)
    except Exception as exc:  # noqa: BLE001 — старт важнее сверки
        logging.getLogger("toontoon.wallet").warning(
            "Сверка возвратов не прошла (%s). Повторится на следующем запуске.", exc
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
    warn_if_nobody_is_watching()
    await settle_unpaid_refunds()

    yield
    await db.disconnect()
    await disconnect()


app = FastAPI(
    title=settings.app_name,
    version="1.0.0",
    description="Identity, wallet and AI content generation for TOONTOON.",
    lifespan=lifespan,
    # Описание API наружу не отдаём. `/openapi.json` перечисляет все ручки со
    # схемами тел — то есть выдаёт готовую карту поверхности всякому, кто
    # спросит. Разработчику оно нужно, публике — нет, и включается вместе с
    # `DEBUG`, а не забывается вместе с ним.
    docs_url="/docs" if settings.debug else None,
    redoc_url="/redoc" if settings.debug else None,
    openapi_url="/openapi.json" if settings.debug else None,
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
    """Жив ли процесс. Ничего больше.

    Нарочно не знает ни про базу, ни про отказы: эту ручку опрашивает
    балансировщик, и «невозвращённый TOONTOON» не повод выкинуть из ротации
    работающий сервис. Диагноз — рядом, отдельным путём.
    """
    return {"status": "ok", "app": settings.app_name}


@app.get("/health/pulse", tags=["meta"])
async def pulse(token: str = "") -> JSONResponse:
    """Диагноз для сторожевого сервиса: 200 — всё в порядке, 503 — нет.

    Код ответа, а не тело, потому что тревогу должен поднимать любой
    бесплатный аптайм-монитор из коробки, без разбора JSON и без настройки
    правил. Тело — для человека, который придёт по ссылке из письма.

    Без токена ручки нет вовсе, и неверный токен отвечает тем же 404: иначе
    ответ сам сообщал бы, что за этим путём что-то есть.
    """
    good = settings.watchdog_token
    # В байтах, а не в строках: compare_digest на не-ASCII бросает
    # TypeError, и токен русскими буквами ронял бы ручку пятисоткой.
    if not good or not secrets.compare_digest(token.encode(), good.encode()):
        raise HTTPException(status_code=404)

    async with db.session_scope() as session:
        d = await diagnosis.diagnose(session)

    if not d.ok:
        logging.getLogger("toontoon.watchdog").warning(
            "сторожу отвечено «нехорошо»: %s", "; ".join(d.reasons))
    return JSONResponse(d.as_dict(), status_code=200 if d.ok else 503)
