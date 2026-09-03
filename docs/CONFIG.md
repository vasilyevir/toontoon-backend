# Настройки

**Сгенерировано из `app/config.py`** — не редактируй руками, правь код
и прогоняй `python -m scripts.dump_reference`.

Любая настройка задаётся переменной окружения с тем же именем в верхнем
регистре. Значения по умолчанию рассчитаны на локальную разработку:
в кластере переопределяются через ConfigMap, секреты — через Secret.

| Переменная | Тип | По умолчанию |
| --- | --- | --- |
| `APP_NAME` | str | `TOONTOON API` |
| `DEBUG` | bool | `False` |
| `CORS_ORIGINS` | str | `http://localhost:3000,http://127.0.0.1:3000` |
| `PUBLIC_BASE_URL` | str | «задаётся в окружении» |
| `FRONTEND_URL` | str | «задаётся в окружении» |
| `AUTH_SUCCESS_REDIRECT` | str | `/generate` |
| `PERSONALISE_CATALOGUE` | bool | `False` |
| `HIDDEN_CATEGORIES` | str | `fantasy_mode,family_fun` |
| `APP_KEY` | str | пусто |
| `APP_SECRET` | str | пусто |
| `APP_KEY_REQUIRED` | bool | `False` |
| `APP_SIG_MAX_SKEW_SECONDS` | int | `300` |
| `TRUSTED_PROXY_COUNT` | int | `0` |
| `MODEL_CALLS_PER_HOUR` | int | `60` |
| `UPLOADS_PER_HOUR` | int | `120` |
| `GENERATION_LOCK_SECONDS` | int | `300` |
| `GUESTS_PER_HOUR` | int | `10` |
| `MAX_UPLOAD_MB` | int | `15` |
| `GUEST_MEDIA_RETENTION_DAYS` | int | `180` |
| `WATCHDOG_TOKEN` | str | пусто |
| `APP_KEY_EXEMPT_PREFIXES` | str | «задаётся в окружении» |
| `REDIS_URL` | str | «задаётся в окружении» |
| `USE_FAKE_REDIS` | bool | `False` |
| `DATABASE_URL` | str | «задаётся в окружении» |
| `DATABASE_ECHO` | bool | `False` |
| `DATABASE_POOL_SIZE` | int | `10` |
| `DATABASE_MAX_OVERFLOW` | int | `5` |
| `STORAGE_BACKEND` | str | `s3` |
| `S3_ENDPOINT_URL` | str | «задаётся в окружении» |
| `S3_REGION` | str | `us-east-1` |
| `S3_BUCKET` | str | `toontoon-dev` |
| `S3_ACCESS_KEY` | str | «задаётся в окружении» |
| `S3_SECRET_KEY` | str | «задаётся в окружении» |
| `S3_SIGNED_URL_TTL_SECONDS` | int | `900` |
| `THUMBNAIL_MAX_SIDE` | int | `512` |
| `MAX_IMAGE_PIXELS` | int | `24000000` |
| `REFUND_SWEEP_INTERVAL_SECONDS` | int | `300` |
| `POLICY_ENABLED` | bool | `True` |
| `MINOR_SAFE_CATEGORIES` | str | `ai_photo_studio,black_and_white,cartoon_me,polaroid_reunion,family_fun,pet_magic` |
| `POLICY_REFUSALS_PER_DAY` | int | `5` |
| `SUPPORT_EMAIL` | str | `hello@toontoon.ai` |
| `THUMBNAIL_QUALITY` | int | `82` |
| `SESSION_COOKIE_NAME` | str | `toontoon-session` |
| `SESSION_TTL_DAYS` | int | `30` |
| `MAGIC_LINK_TTL_MINUTES` | int | `15` |
| `PASSWORD_RESET_TTL_MINUTES` | int | `60` |
| `SESSION_COOKIE_SAMESITE` | str | `lax` |
| `SESSION_COOKIE_SECURE` | bool | `False` |
| `SIGNUP_TOONTOON_BALANCE` | int | `30` |
| `DAILY_REWARD_SCHEDULE` | str | `10,10,10,10,10,20,30` |
| `FREE_BALANCE_CAP` | int | `300` |
| `FREE_WEEKLY_QUOTA` | int | `100` |
| `EXPOSE_DEV_TOKENS` | bool | `False` |
| `STALE_GENERATION_MINUTES` | int | `30` |
| `CHAT_CONTEXT_MESSAGES` | int | `20` |
| `OPENAI_API_KEY` | str | пусто |
| `OPENAI_MODEL` | str | `gpt-4o-mini` |
| `OPENAI_IMAGE_MODEL` | str | `gpt-image-1` |
| `OPENAI_IMAGE_SIZE` | str | `1024x1536` |
| `OPENAI_IMAGE_QUALITY` | str | `medium` |
| `OPENAI_IMAGE_TIMEOUT` | float | `55.0` |
| `RATE_LIMIT_PER_HOUR` | int | `30` |
| `IMAGE_TOONTOON_COST` | int | `15` |
| `VIDEO_TOONTOON_COST` | int | `175` |
| `FAL_API_KEY` | str | пусто |
| `FAL_BASE_URL` | str | «задаётся в окружении» |
| `FAL_IMAGE_MODEL` | str | `fal-ai/nano-banana` |
| `FAL_REQUEST_TIMEOUT` | float | `150.0` |
| `FAL_HTTP_TIMEOUT` | float | `30.0` |
| `FAL_CHUNK_BYTES` | int | `16384` |
| `FAL_CHUNK_TIMEOUT` | float | `10.0` |
| `FAL_CHUNK_RETRIES` | int | `3` |
| `FAL_DOWNLOAD_DEADLINE` | float | `180.0` |
| `FAL_DOWNLOAD_HOSTS` | str | `fal.media,fal.run,fal.ai` |
| `FAL_DOWNLOAD_MAX_BYTES` | int | `67108864` |
| `FAL_POLL_INTERVAL` | float | `1.5` |
| `FAL_OUTPUT_FORMAT` | str | `png` |
| `FAL_MEDIA_TTL_SECONDS` | int | `300` |
| `FAL_ASPECT_RATIO` | str | `9:16` |
| `OPENROUTER_API_KEY` | str | пусто |
| `OPENROUTER_BASE_URL` | str | «задаётся в окружении» |
| `OPENROUTER_IMAGE_MODEL` | str | `google/gemini-3.1-flash-image` |
| `OPENROUTER_ASPECT_RATIO` | str | `9:16` |
| `OPENROUTER_RESOLUTION` | str | `1K` |
| `OPENROUTER_OUTPUT_FORMAT` | str | `png` |
| `OPENROUTER_QUALITY` | str | `medium` |
| `OPENROUTER_TEXT_MODEL` | str | `openai/gpt-4o-mini` |
| `SLOT_EXTRACTION_MODEL` | str | `google/gemini-2.5-flash` |
| `PROFILE_REFERENCE_COUNT` | int | `1` |
| `OPENROUTER_REQUEST_TIMEOUT` | float | `90.0` |
| `VIDEO_ENABLED` | bool | `False` |
| `KIE_API_KEY` | str | пусто |
| `KIE_BASE_URL` | str | «задаётся в окружении» |
| `KIE_VIDEO_MODEL` | str | `bytedance/seedance-2-fast` |
| `KIE_VIDEO_AUDIO_MODEL` | str | `bytedance/seedance-2` |
| `VIDEO_FRAME_COUNT` | int | `4` |
| `VIDEO_RESOLUTION` | str | `720p` |
| `VIDEO_ASPECT_RATIO` | str | `9:16` |
| `VIDEO_DURATION` | int | `5` |
| `VIDEO_POLL_INTERVAL` | float | `6.0` |
| `VIDEO_POLL_TIMEOUT` | float | `900.0` |
| `VAPID_PRIVATE_KEY` | str | пусто |
| `VAPID_PUBLIC_KEY` | str | пусто |
| `VAPID_EMAIL` | str | `mailto:hello@toontoon.ai` |
| `CLOUDINARY_CLOUD_NAME` | str | пусто |
| `CLOUDINARY_API_KEY` | str | пусто |
| `CLOUDINARY_API_SECRET` | str | пусто |
| `GOOGLE_CLIENT_ID` | str | пусто |
| `GOOGLE_CLIENT_SECRET` | str | пусто |
| `GOOGLE_REDIRECT_URI` | str | пусто |
| `ACCEPT_STOREKIT_TEST_ROOT` | bool | `False` |
| `APPLE_BUNDLE_ID` | str | пусто |
| `APPLE_SERVICE_ID` | str | пусто |
| `APP_DEEP_LINK_SCHEME` | str | `toontoon` |

## Что обязательно переопределить в проде

- `DATABASE_URL` — иначе приложение пойдёт искать базу на localhost.
- `EXPOSE_DEV_TOKENS=false` — иначе токен сброса пароля уходит в ответе API.
- `DEBUG=false`, `SESSION_COOKIE_SECURE=true`.
- `S3_*` — доступ к объектному хранилищу и имя бакета для окружения.
- `PUBLIC_BASE_URL`, `FRONTEND_URL`, `CORS_ORIGINS` — настоящие адреса.
