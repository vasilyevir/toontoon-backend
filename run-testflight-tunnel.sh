#!/usr/bin/env bash
# Локальный бэкенд для сборки на телефон через публичный туннель.
#
# Зачем: адрес MacBook-Pro-Ila.local заставляет iOS спрашивать разрешение на
# поиск в локальной сети — окно, которого в продукте быть не должно (Илья,
# 2026-09-08). Публичный https-адрес туннеля его не вызывает, а TLS снимает сам
# туннель, так что сервер слушает голый HTTP на петле (PLAIN=1).
#
# Почему pinggy: cloudflared из этой сети не держит связь (QUIC не проходит,
# http2 рвётся каждые полминуты), localhost.run закрывает сессию сразу после
# входа. Pinggy идёт по ssh на 443 и держится. Цена: бесплатный туннель живёт
# 60 минут, адрес при перезапуске меняется, а он вшит в сборку — после каждого
# перезапуска приложение собирается заново с новым TOONTOON_BASE_URL.
#
# Использование: ./run-testflight-tunnel.sh   (PORT=8444 по умолчанию)
# Печатает строку для Config.testflight.xcconfig; пин не нужен — сертификат
# у туннеля настоящий.
set -euo pipefail
cd "$(dirname "$0")"
PORT="${PORT:-8444}"
LOG="${TUNNEL_LOG:-/tmp/toontoon-pinggy.log}"

pkill -f "R0:localhost:$PORT a.pinggy.io" 2>/dev/null || true
pkill -f "port $PORT" 2>/dev/null || true
sleep 1
: > "$LOG"
ssh -p 443 -o StrictHostKeyChecking=accept-new -o ServerAliveInterval=30 \
    -R0:localhost:"$PORT" a.pinggy.io > "$LOG" 2>&1 &
for _ in $(seq 1 40); do
  grep -qoE "https://[a-z0-9-]+\.free\.pinggy\.net" "$LOG" && break
  sleep 1
done
BASE="$(grep -oE "https://[a-z0-9-]+\.free\.pinggy\.net" "$LOG" | head -1 || true)"
[ -n "$BASE" ] || { echo "туннель не поднялся, см. $LOG"; exit 1; }

echo "▶ $BASE  (живёт 60 минут)"
echo "  Config.testflight.xcconfig: TOONTOON_BASE_URL = https:/\$()/${BASE#https://} ; TOONTOON_PINNED_SPKI ="
exec env PLAIN=1 PORT="$PORT" BASE="$BASE" ./run-testflight-local.sh
