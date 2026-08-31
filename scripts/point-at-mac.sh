#!/usr/bin/env bash
# Свести адрес мака в трёх местах разом.
#
# Адрес меняется сам: телефонный хотспот даёт 172.20.10.x, домашний Wi-Fi —
# 192.168.x.x, и переключение между ними ломает отладку на устройстве молча.
# Приложение продолжает стучаться по зашитому адресу, сервер отвечает по
# новому, и на телефоне это выглядит вечной загрузкой — то есть как поломка
# бэкенда, которой нет.
#
# Трижды за два дня диагноз занимал больше времени, чем починка. Поэтому
# отдельным скриптом, а не памятью.
#
#   ./scripts/point-at-mac.sh          # вписать текущий адрес в Config.xcconfig
#   ./scripts/point-at-mac.sh --check  # только сказать, сходится ли
#
# Имена переменных латиницей не по вкусу: bash кириллицу в них не принимает.
set -euo pipefail
cd "$(dirname "$0")/.."

config="../../arteki-ios-app/Config.xcconfig"
port="${PORT:-8020}"

addr=""
for iface in en0 en1; do
    addr=$(ipconfig getifaddr "$iface" 2>/dev/null) || addr=""
    [ -n "$addr" ] && break
done
[ -n "$addr" ] || { echo "Сети нет: ни en0, ни en1 не дали адреса." >&2; exit 1; }
[ -f "$config" ] || { echo "Не нашёл $config" >&2; exit 1; }

# В xcconfig `//` начинает комментарий, поэтому его разбивают через /$()/.
want="http:/\$()/$addr:$port"
have=$(grep -oE '^TOONTOON_BASE_URL = .*' "$config" | sed 's/^TOONTOON_BASE_URL = //')

if [ "$have" = "$want" ]; then
    echo "Сходится: приложение смотрит на $addr:$port"
    exit 0
fi

if [ "${1:-}" = "--check" ]; then
    echo "РАСХОЖДЕНИЕ: мак на $addr, приложение собрано под $(printf '%s' "$have" | sed 's|/[$]()/|//|')" >&2
    echo "Починить: ./scripts/point-at-mac.sh, затем пересобрать." >&2
    exit 1
fi

/usr/bin/sed -i '' -E "s|^TOONTOON_BASE_URL = .*|TOONTOON_BASE_URL = $want|" "$config"
echo "Вписал $addr:$port в Config.xcconfig."
echo
echo "Дальше — поднять сервер на тот же адрес и пересобрать приложение:"
echo "  BASE=\"http://$addr:$port\" ./run-local.sh"
