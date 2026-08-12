# Локальная разработка

## Быстрый старт

```bash
docker start arteki-postgres arteki-redis arteki-minio   # или создать, см. ниже
.venv/bin/python -m alembic upgrade head
PYTHONPATH=. .venv/bin/python -m app.db.seed             # тарифы и провайдеры
./run-local.sh                                           # http://localhost:8020
```

Проверка, что всё поднялось:

```bash
curl -s localhost:8020/health
curl -s -X POST localhost:8020/api/auth/guest            # должен вернуть сессию
```

## Порты и почему они такие

| Что | Порт | Почему не стандартный |
| --- | --- | --- |
| Бэкенд | 8020 | 8000 занят контейнером gbr-backend, 8010 — aeo-analyzer |
| PostgreSQL | 5433 | 5432 обычно занят чужим проектом |
| Redis | 6379 | свободен |
| MinIO (S3) | 9100 | 9000 занят MinIO соседнего проекта |
| MinIO (консоль) | 9101 | http://localhost:9101, логин `arteki` / `arteki-dev-secret` |

Все контейнеры слушают только `127.0.0.1` — снаружи машины до них не достучаться.

## Создать контейнеры с нуля

```bash
docker run -d --name arteki-postgres -p 127.0.0.1:5433:5432 \
  -e POSTGRES_USER=arteki -e POSTGRES_PASSWORD=arteki -e POSTGRES_DB=arteki \
  postgres:16-alpine

docker run -d --name arteki-redis -p 127.0.0.1:6379:6379 redis:7-alpine

docker run -d --name arteki-minio -p 127.0.0.1:9100:9000 -p 127.0.0.1:9101:9001 \
  -e MINIO_ROOT_USER=arteki -e MINIO_ROOT_PASSWORD=arteki-dev-secret \
  minio/minio:RELEASE.2024-11-07T00-52-20Z server /data --console-address ":9001"
```

Бакет создаётся приложением при старте — руками ничего заводить не нужно.

## Про `.env` и `run-local.sh`

`.env` в репозитории — конфиг боевого сервера. **Его не трогаем.** Переменные
окружения в pydantic-settings побеждают `.env`, поэтому `run-local.sh` просто
перекрывает адреса на локальные: `PUBLIC_BASE_URL`, `DATABASE_URL`, CORS,
`APPLE_BUNDLE_ID`. Так локальный запуск не может случайно уехать в прод.

Без Redis можно обойтись: `USE_FAKE_REDIS=true ./run-local.sh` — сессии
не переживут перезапуск, но всё остальное работает.

## Миграции

```bash
.venv/bin/python -m alembic revision --autogenerate -m "что сделал"
.venv/bin/python -m alembic upgrade head
```

URL берётся из настроек, не из `alembic.ini`, — одни и те же миграции
применяются локально и в кластере.

## Тесты

```bash
PYTHONPATH=. .venv/bin/python -m pytest tests -q
```

Тесты идут **против настоящего PostgreSQL**, а не против подделки: половина
проверяемых гарантий — уникальные индексы и транзакции, на моке они
не проверяются вовсе. Данные за собой тесты убирают.

## Справочная документация

```bash
PYTHONPATH=. .venv/bin/python -m scripts.dump_reference          # обновить
PYTHONPATH=. .venv/bin/python -m scripts.dump_reference --check  # сверить
```

`openapi.json` и `CONFIG.md` генерируются из кода. `--check` возвращает
ненулевой код, если они отстали, — это то, что вешается в CI, чтобы
документация не протухала молча.

## Приложение iOS против локального сервера

В `Config.xcconfig` уже стоит `ARTEKI_BASE_URL = http://localhost:8020`,
пиннинг выключен, в `Info.plist` добавлен `NSAllowsLocalNetworking`.
На симуляторе работает как есть; для устройства в той же сети подставь IP
мака (`ipconfig getifaddr en0`).

## Что не работает локально и почему

**Видео** выключено флагом `video_enabled=false` — оно вне первой версии.

**Письма** не отправляются: SMTP появится вместе с кластером. Пока
`EXPOSE_DEV_TOKENS=true` возвращает токены сброса прямо в ответе API.

**Google Sign-In** отвечает 503: `GOOGLE_CLIENT_ID` пустой. Apple работает,
если задан `APPLE_BUNDLE_ID` (`run-local.sh` его выставляет).
