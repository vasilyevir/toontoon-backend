#!/usr/bin/env bash
# Локальный бэкенд под сборку из TestFlight — пока нет своего сервера.
#
# Релизная сборка приложения ходит только по HTTPS и на IP/локальный хост —
# только с пином (APISecurity.swift). Поэтому здесь uvicorn сам терминирует TLS
# самоподписанным сертификатом на имя этого мака, а пин от него уезжает в
# Config.xcconfig (TOONTOON_PINNED_SPKI). Телефон должен быть в той же Wi-Fi.
#
#   ./run-testflight-local.sh            # https://<этот-мак>.local:8443
#   ./run-testflight-local.sh --pin      # только напечатать пин и выйти
#
# Флаги — боевые (DEBUG=false, APP_KEY_REQUIRED=true), кроме песочницы App
# Store: TestFlight покупает в sandbox, ACCEPT_SANDBOX_RECEIPTS=true.
# Не --reload: боевые флаги + автоперезапуск дают ложные «сервер упал».
set -euo pipefail
cd "$(dirname "$0")"

HOST_NAME="${HOST_NAME:-$(hostname -s).local}"
PORT="${PORT:-8443}"
TLS=.local-tls                               # в .gitignore
mkdir -p "$TLS"
if [ ! -f "$TLS/$HOST_NAME.crt" ]; then
  # RSA-2048 намеренно: APISecurity.spkiSHA256Base64 считает пин по заголовку
  # SPKI именно RSA-2048; для EC-ключа пин не совпадёт.
  openssl req -x509 -newkey rsa:2048 -nodes -days 825 -sha256 \
    -subj "/CN=$HOST_NAME" -addext "subjectAltName=DNS:$HOST_NAME" \
    -keyout "$TLS/$HOST_NAME.key" -out "$TLS/$HOST_NAME.crt" 2>/dev/null
fi
pin() {
  openssl x509 -in "$TLS/$HOST_NAME.crt" -pubkey -noout \
    | openssl pkey -pubin -outform der | openssl dgst -sha256 -binary | base64
}
if [ "${1:-}" = "--pin" ]; then pin; exit 0; fi

for k in APP_KEY APP_SECRET; do
  grep -qE "^$k=\S" .env || { echo "в .env пуст $k — приложение подписывает запросы им же"; exit 1; }
done

BASE="https://$HOST_NAME:$PORT"
export PUBLIC_BASE_URL="$BASE"
export CORS_ORIGINS="$BASE"
export DATABASE_URL="${DATABASE_URL:-postgresql+asyncpg://toontoon:toontoon@localhost:5433/toontoon}"
export APPLE_BUNDLE_ID="${APPLE_BUNDLE_ID:-ai.toontoon.ios}"
export DEBUG=false
export EXPOSE_DEV_TOKENS=false
export ACCEPT_STOREKIT_TEST_ROOT=false
export ACCEPT_SANDBOX_RECEIPTS=true
export APP_KEY_REQUIRED=true
export SESSION_COOKIE_SECURE=true
export WORKER_MODE="${WORKER_MODE:-inline}"

echo "▶ $BASE   пин: $(pin)"
echo "  Config.xcconfig: TOONTOON_BASE_URL = https:/\$()/$HOST_NAME:$PORT ; TOONTOON_PINNED_SPKI = $(pin)"
exec .venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port "$PORT" \
  --ssl-certfile "$TLS/$HOST_NAME.crt" --ssl-keyfile "$TLS/$HOST_NAME.key" \
  --no-proxy-headers --no-server-header
