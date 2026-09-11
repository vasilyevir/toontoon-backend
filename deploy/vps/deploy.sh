#!/usr/bin/env bash
# Обновление бэкенда на сервере. Зовётся из GitHub Actions по ssh, но и руками
# работает так же: ./deploy.sh [окружение]
#
# Порядок важен и выбран по цене ошибки:
#   собрать → поднять → миграции → каталог → проверить здоровье.
# Сборка и миграции идут ДО того, как новый код начнёт отвечать людям, а
# проверка здоровья в конце — чтобы неудачный выкат было видно сразу, а не
# от первого письма в поддержку.
#
# Идемпотентно: повторный запуск на том же коде ничего не ломает.
set -euo pipefail

ENVIRONMENT="${1:-production}"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
COMPOSE_DIR="$ROOT/deploy/vps"
ENV_FILE="$COMPOSE_DIR/.env.prod"
# Имя проекта Docker определяет имена томов: сменить его — значит потерять
# базу. Держим его привязанным к окружению и НЕ меняем задним числом.
PROJECT="${COMPOSE_PROJECT_NAME:-toontoon-${ENVIRONMENT}}"

cd "$COMPOSE_DIR"
[ -f "$ENV_FILE" ] || { echo "нет $ENV_FILE — секреты на сервер кладутся руками, не из репозитория"; exit 1; }

compose() { sudo docker compose -p "$PROJECT" --env-file "$ENV_FILE" "$@"; }

echo "▶ $ENVIRONMENT: сборка образа"
compose build api

echo "▶ поднимаю сервисы"
compose up -d

echo "▶ миграции"
compose exec -T -e PYTHONPATH=/app api alembic upgrade head

echo "▶ каталог стилей"
compose exec -T -e PYTHONPATH=/app api python scripts/import_styles.py --apply | tail -1

echo "▶ тарифы и провайдеры"
compose exec -T -e PYTHONPATH=/app api python -m app.db.seed | tail -1

echo "▶ проверка здоровья"
for attempt in $(seq 1 30); do
    code=$(curl -s -o /dev/null -m 5 -w "%{http_code}" http://127.0.0.1:8000/health || true)
    if [ "$code" = "200" ]; then
        echo "✓ $ENVIRONMENT обновлён: $(git -C "$ROOT" rev-parse --short HEAD 2>/dev/null || echo "без git")"
        exit 0
    fi
    sleep 2
done

echo "✗ сервис не ответил 200 за минуту — смотри логи:"
compose logs --tail 40 api
exit 1
