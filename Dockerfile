# ── TOONTOON backend (FastAPI) ───────────────────────────────────────────────────
# NOTE for DevOps:
#   - Requires Redis (all persistence — users/sessions/generations — lives in
#     Redis, there is no SQL DB). Set redis.enabled: true in deploy/values.yaml
#     or point REDIS_URL at an external instance via the cluster Secret.
#   - Requires ffmpeg at runtime (video thumbnail extraction) — installed below.
#   - Health check: GET /health → {"status": "ok", ...}.
#   - Secrets (OpenAI/Kie.ai/Boostify/Cloudinary/VAPID/session) come from the
#     Secret named "<release>-secrets" (secret.enabled: true) — see repo README
#     / PR description for the exact key list, never committed here.

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
