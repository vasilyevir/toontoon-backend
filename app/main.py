"""ARTEKI backend — FastAPI application entrypoint."""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.middleware.app_key import AppKeyMiddleware
from app.redis_client import connect, disconnect
from app.routers import (
    app_meta,
    auth,
    chat,
    chats,
    events,
    generate,
    generations,
    payments,
    profile,
    push,
    tiles,
    webhooks,
)

UPLOAD_DIR = Path("uploads")


@asynccontextmanager
async def lifespan(app: FastAPI):
    UPLOAD_DIR.mkdir(exist_ok=True)
    await connect()
    yield
    await disconnect()


app = FastAPI(
    title=settings.app_name,
    version="1.0.0",
    description="Identity, wallet and AI content generation for ARTEKI.",
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

# Serve uploaded reference photos.
app.mount("/uploads", StaticFiles(directory=str(UPLOAD_DIR), check_dir=False), name="uploads")

app.include_router(auth.router)
app.include_router(app_meta.router)
app.include_router(profile.router)
app.include_router(chat.router)
app.include_router(chats.router)
app.include_router(tiles.router)
app.include_router(generate.router)
app.include_router(generations.router)
app.include_router(payments.router)
app.include_router(push.router)
app.include_router(events.router)
app.include_router(webhooks.router)


@app.get("/health", tags=["meta"])
async def health() -> dict:
    return {"status": "ok", "app": settings.app_name}
