# ── TOONTOON backend (FastAPI) ───────────────────────────────────────────────
# Что нужно этому образу, чтобы стать работающим сервисом.
#
# PostgreSQL — система записи. Пользователи, кошелёк, генерации, разговоры и
#   профили лиц живут здесь; DATABASE_URL обязателен, без него не поднимется
#   ни одна ручка. Раньше в этой шапке было написано «SQL-базы нет, всё в
#   Redis» — так было до миграции на Postgres, и год спустя это читалось как
#   инструкция.
#
#   Схему накатывает `alembic upgrade head` — ДО старта пода и один раз на
#   релиз, а не в CMD: реплик несколько, и они возьмут одну миграцию наперегонки.
#   В чарте за это отвечает initSchema (deploy/values.yaml).
#
# Redis — сессии, кэш, ограничители частоты и замки. Не система записи:
#   потеря Redis разлогинивает, но ничего не теряет.
#
# S3 (в кластере MinIO) — снимки и результаты. STORAGE_BACKEND=s3;
#   "local" пишет в ./uploads и приватность обеспечить не может — только для
#   разработки. Отдаются короткие подписанные ссылки, не сырые URL объектов.
#
# ffmpeg — кадр-обложка для видео. Ставится ниже.
#
# Здоровье: GET /health → {"status": "ok", ...}.
#
# Секреты (DATABASE_URL, REDIS_URL, OPENROUTER_API_KEY, S3_*, APP_SECRET,
#   VAPID_*) приходят из Secret "<release>-secrets". Полный список того, что
#   читает код, — в .env.example; здесь их нет и не будет.

FROM python:3.12-slim AS base
WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN mkdir -p uploads

RUN useradd --system --uid 1001 appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
