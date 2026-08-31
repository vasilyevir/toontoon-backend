#!/usr/bin/env bash
# Локальный запуск нового бэкенда для отладки iOS-приложения.
#
# .env в этой папке — конфиг боевого сервера (PUBLIC_BASE_URL=193.149.190.155).
# Мы его НЕ трогаем: переменные окружения в pydantic-settings побеждают .env,
# поэтому здесь просто перекрываем адреса на локальные.
#
#   ./run-local.sh            # http://0.0.0.0:8020
#   PORT=8080 ./run-local.sh  # другой порт
#
# Порт 8000 занят контейнером gbr-backend, 8010 — aeo-analyzer, поэтому дефолт 8020.
# Redis: контейнер `toontoon-redis` (docker start toontoon-redis), либо
# USE_FAKE_REDIS=true ./run-local.sh — без Redis вообще (данные не переживут рестарт).
#
# Postgres: контейнер `toontoon-postgres` на порту 5433 (5432 обычно занят чужим
# проектом). Поднять: docker start toontoon-postgres
# Миграции:  .venv/bin/python -m alembic upgrade head
set -euo pipefail
cd "$(dirname "$0")"

PORT="${PORT:-8020}"
# Перекрывается, как и всё остальное рядом. Нужно для отладки на устройстве:
# ссылки на картинки строятся из PUBLIC_BASE_URL, и с localhost телефон их не
# загрузит — сервер он найдёт, а картинки нет, и каталог будет пустой рамкой.
#   BASE="http://$(ipconfig getifaddr en0):8020" ./run-local.sh
BASE="${BASE:-http://localhost:${PORT}}"

export PUBLIC_BASE_URL="$BASE"
export FRONTEND_URL="${FRONTEND_URL:-http://localhost:3000}"
export CORS_ORIGINS="${CORS_ORIGINS:-http://localhost:3000,http://127.0.0.1:3000,${BASE}}"
export BOOSTIFY_REDIRECT_URI="${BOOSTIFY_REDIRECT_URI:-${BASE}/api/auth/boostify/callback}"
export SESSION_COOKIE_SECURE=false
export DATABASE_URL="${DATABASE_URL:-postgresql+asyncpg://toontoon:toontoon@localhost:5433/toontoon}"
# Sign in with Apple: секрета на нашей стороне не нужно, достаточно знать
# audience = Bundle ID приложения. Без этого /api/app/bootstrap отдаёт
# apple_enabled=false и апка прячет кнопку Apple.
export APPLE_BUNDLE_ID="${APPLE_BUNDLE_ID:-ai.toontoon.ios}"
export APP_KEY_REQUIRED="${APP_KEY_REQUIRED:-false}"
export DEBUG=true

exec .venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port "$PORT" --reload
