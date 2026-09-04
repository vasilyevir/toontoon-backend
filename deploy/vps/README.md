# Прототип для TestFlight на своём VPS

Не прод. Задача одна: чтобы сборка из TestFlight работала у людей вне нашей
сети. Домен и App Review здесь не нужны — внутренним тестировщикам TestFlight
достаточно рабочего сервера по HTTPS. Серт самоподписанный, приложение
принимает его по пину (`TOONTOON_PINNED_SPKI`).

## Какой сервер

Свой, новый, только под Toontoon. Хватит 2 vCPU / 4 GB / 40 GB SSD с Ubuntu 24.04
и публичным IPv4: сервер сам ничего не рисует, он ждёт fal и держит Postgres,
Redis и MinIO. Домен не нужен — приложение идёт на IP по HTTPS с пином.

## Шаги

0. Серт: `openssl req -x509 -newkey rsa:2048 -days 1095 -nodes -subj "/CN=<IP>" -addext "subjectAltName=IP:<IP>" -keyout /etc/nginx/ssl/toontoon.key -out /etc/nginx/ssl/toontoon.crt`
   (папку создать заранее). Пин для приложения — `scripts/pin.sh <IP>` в iOS-репо.
1. Docker: `curl -fsSL https://get.docker.com | sh`.
2. Код: `git clone` репозитория в `/opt/toontoon`, `cd deploy/vps`.
3. `.env.prod` — из `.env.prod.example`; ключи провайдеров те же, что локально.
4. `docker compose --env-file .env.prod up -d --build`, затем
   `docker compose --env-file .env.prod run --rm api alembic upgrade head` и
   `docker compose --env-file .env.prod run --rm api python -m app.db.seed`.
5. Бакет: `docker compose exec minio mc alias set local http://localhost:9000 $USER $PASS && mc mb local/toontoon`
   (или через API MinIO при первом обращении — смотри `app/storage`).
6. nginx: `apt install nginx`, положить `nginx-toontoon.conf` в `sites-enabled`,
   убрать `default`, `nginx -t && systemctl reload nginx`.
7. Проверка: `curl -k https://<IP>/health`, затем подписанный
   `POST /api/auth/guest` (см. `app/middleware/app_key.py`).

## Приложение

В `Config.xcconfig` для архива: `TOONTOON_BASE_URL = https://<IP>`,
`TOONTOON_PINNED_SPKI` = отпечаток серта (см. `scripts/testflight.sh` в iOS-репо —
он его печатает), `TOONTOON_APP_KEY/SECRET` = `APP_KEY/SECRET` сервера.
