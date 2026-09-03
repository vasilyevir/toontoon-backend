# Прототип для TestFlight на VPS 193.149.190.155

Не прод. Задача одна: чтобы сборка из TestFlight работала у людей вне нашей
сети. Домен и App Review здесь не нужны — внутренним тестировщикам TestFlight
достаточно рабочего сервера по HTTPS. Серт самоподписанный, приложение
принимает его по пину (`TOONTOON_PINNED_SPKI`).

## Что на сервере сейчас и что с ним делать

Старый бэкенд в `/opt/gen-backend` (systemd `arteki-backend`, без Postgres, с
публичным `/uploads/`) и старый веб-фронт (`arteki-frontend`, Next.js). Оба
погасить: `systemctl disable --now arteki-backend arteki-frontend`. Ключи из
`/opt/gen-backend/.env` отозвать — они лежали там с `DEBUG=true`.

## Шаги

1. Docker: `curl -fsSL https://get.docker.com | sh` (на сервере его нет).
2. Код: `git clone` репозитория в `/opt/toontoon`, `cd deploy/vps`.
3. `.env.prod` — из `.env.prod.example`; ключи провайдеров те же, что локально.
4. `docker compose --env-file .env.prod up -d --build`, затем
   `docker compose --env-file .env.prod run --rm api alembic upgrade head` и
   `docker compose --env-file .env.prod run --rm api python -m app.db.seed`.
5. Бакет: `docker compose exec minio mc alias set local http://localhost:9000 $USER $PASS && mc mb local/toontoon`
   (или через API MinIO при первом обращении — смотри `app/storage`).
6. nginx: положить `nginx-toontoon.conf` в `sites-enabled`, убрать `arteki`
   и `arteki-ssl`, `nginx -t && systemctl reload nginx`.
7. Проверка: `curl -k https://193.149.190.155/health`, затем подписанный
   `POST /api/auth/guest` (см. `app/middleware/app_key.py`).

## Приложение

В `Config.xcconfig` для архива: `TOONTOON_BASE_URL = https://193.149.190.155`,
`TOONTOON_PINNED_SPKI` = отпечаток серта (см. `scripts/testflight.sh` в iOS-репо —
он его печатает), `TOONTOON_APP_KEY/SECRET` = `APP_KEY/SECRET` сервера.
