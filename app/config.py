from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application configuration, loaded from environment / .env file."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # App
    app_name: str = "ARTEKI API"
    debug: bool = True
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"
    public_base_url: str = "http://localhost:8000"
    frontend_url: str = "http://localhost:3000"
    auth_success_redirect: str = "/generate"

    # Mobile app-key + HMAC request signing (see docs/APP-KEY-SETUP.md).
    # DISABLED by default — flipping app_key_required to true rejects any /api/*
    # request that isn't signed by the app, so enable ONLY once mobile is verified
    # and web/webhooks are excluded or also signing. Values must match the app's
    # Config.xcconfig (APP_KEY / APP_SECRET).
    app_key: str = ""
    app_secret: str = ""
    app_key_required: bool = False
    app_sig_max_skew_seconds: int = 300
    # Paths under /api that are NEVER signature-checked (server-to-server or
    # signed-over-empty-body): uploads (multipart) + webhooks (external callers).
    app_key_exempt_prefixes: str = "/api/uploads,/api/webhooks"

    @property
    def app_key_exempt_list(self) -> list[str]:
        return [p.strip() for p in self.app_key_exempt_prefixes.split(",") if p.strip()]

    # Redis — sessions, cache, rate limits, locks. No longer a system of record.
    redis_url: str = "redis://localhost:6379/0"
    use_fake_redis: bool = False

    # PostgreSQL — everything that must survive a restart (app/db).
    # Port 5433 locally: 5432 is usually taken by another project's container.
    database_url: str = "postgresql+asyncpg://arteki:arteki@localhost:5433/arteki"
    database_echo: bool = False
    database_pool_size: int = 10
    database_max_overflow: int = 5

    # Object storage (app/storage). "local" writes to ./uploads and is meant for
    # development only — it cannot enforce private access. Production uses "s3",
    # which is MinIO in our cluster and works unchanged with any S3-compatible
    # provider if we ever move.
    storage_backend: str = "s3"  # s3 | local
    s3_endpoint_url: str = "http://localhost:9100"  # MinIO; empty for real AWS
    s3_region: str = "us-east-1"
    s3_bucket: str = "arteki-dev"
    s3_access_key: str = "arteki"
    s3_secret_key: str = "arteki-dev-secret"
    # Results are private: clients get a short-lived signed link, never a raw
    # object URL. Long enough to load a gallery screen, short enough that a
    # leaked link is worthless tomorrow.
    s3_signed_url_ttl_seconds: int = 900
    # Thumbnails for the history grid — the full-size image must never be what
    # a 3-across grid downloads.
    thumbnail_max_side: int = 512
    thumbnail_quality: int = 82

    # Sessions
    session_cookie_name: str = "arteki-session"
    session_ttl_days: int = 30
    magic_link_ttl_minutes: int = 15
    password_reset_ttl_minutes: int = 60
    session_cookie_samesite: str = "lax"
    session_cookie_secure: bool = False
    signup_teki_balance: int = 30

    # ── Экономика ────────────────────────────────────────────────────────────
    # Значения взяты из разбора Glam AI (docs/ECONOMY.md) как стартовая точка.
    # Всё это параметры сервера: менять их не должно стоить релиза приложения.
    #
    # Ежедневные награды: дни 1–5 по 10, шестой 20, седьмой 30 — ровно 100
    # за неделю, столько же, сколько бесплатный тариф даёт целиком. Стрик
    # обнуляется при пропуске дня.
    daily_reward_schedule: str = "10,10,10,10,10,20,30"
    # Потолок несгораемой корзины. Награды копятся, и без потолка неактивный
    # пользователь накопил бы за месяц залп дорогих генераций.
    free_balance_cap: int = 300
    # Недельная квота бесплатного тарифа — она же сумма наград за неделю.
    free_weekly_quota: int = 100

    # Возврат токенов в ответе API вместо письма. Пока в кластере нет SMTP,
    # это единственный способ пройти magic-link и сброс пароля.
    # В проде ОБЯЗАН быть false: иначе смена пароля к чужому аккаунту делается
    # одним запросом. Приложение при старте кричит, если DEBUG выключен,
    # а это ещё включено — забыть можно только намеренно.
    expose_dev_tokens: bool = True

    # Сколько последних сообщений уходит в модель (CH-20). Настройка сервера,
    # а не константа: подобрать это можно только на живых разговорах, и менять
    # ради подбора не должно стоить релиза.
    chat_context_messages: int = 20

    @property
    def daily_reward_list(self) -> list[int]:
        return [int(x) for x in self.daily_reward_schedule.split(",") if x.strip()]

    # OpenAI
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    # Image generation via OpenAI Images API.
    openai_image_model: str = "gpt-image-1"  # auto-falls back to dall-e-3 if unavailable
    openai_image_size: str = "1024x1024"
    openai_image_quality: str = "medium"  # gpt-image-1: low/medium/high/auto; dall-e-3: standard/hd
    openai_image_timeout: float = 55.0

    @property
    def openai_enabled(self) -> bool:
        return bool(self.openai_api_key.strip())

    # Generation
    rate_limit_per_hour: int = 30
    image_teki_cost: int = 1
    video_teki_cost: int = 2
    # Image provider: "openai" (gpt-image-1 / dall-e-3) or "pollinations".
    image_provider: str = "openai"
    pollinations_image_url: str = "https://image.pollinations.ai"
    pollinations_text_url: str = "https://text.pollinations.ai"
    pollinations_model: str = "flux"
    # Optional Pollinations API token (register at https://auth.pollinations.ai).
    # Without it the anonymous tier frequently returns 402 under load.
    pollinations_token: str = ""
    pollinations_referrer: str = "arteki"
    vision_timeout_seconds: int = 7

    # Video generation (kie.ai / ByteDance Seedance).
    # ВЫКЛЮЧЕНО в первой версии: все шесть направлений онбординга — про фото,
    # видео в них не упомянуто ни разу, а стоит оно дороже всего ($1.21–1.52
    # за пятисекундный клип). Код остаётся, маршрут закрыт флагом.
    video_enabled: bool = False
    kie_api_key: str = ""
    kie_base_url: str = "https://api.kie.ai"
    kie_video_model: str = "bytedance/seedance-2-fast"
    # Audio-capable model used for nature kinemagraphs (Seedance 2.0 with generate_audio).
    kie_video_audio_model: str = "bytedance/seedance-2"
    video_frame_count: int = 4
    video_resolution: str = "720p"
    video_aspect_ratio: str = "9:16"
    video_duration: int = 5
    video_poll_interval: float = 6.0
    # bytedance/seedance-2 (audio-capable model, used for all text-overlay
    # tiles: morning/inspiring/greeting) occasionally needs more than 10 min.
    video_poll_timeout: float = 900.0

    @property
    def kie_enabled(self) -> bool:
        return bool(self.kie_api_key.strip())

    # Web Push (VAPID)
    vapid_private_key: str = ""
    vapid_public_key: str = ""
    vapid_email: str = "mailto:hello@arteki.ai"

    @property
    def push_enabled(self) -> bool:
        return bool(self.vapid_private_key.strip() and self.vapid_public_key.strip())

    # Cloudinary — video text overlay (bakes text_overlay into the rendered mp4).
    # Leave cloud_name empty to skip overlay (videos delivered without text).
    cloudinary_cloud_name: str = ""
    cloudinary_api_key: str = ""
    cloudinary_api_secret: str = ""

    @property
    def cloudinary_enabled(self) -> bool:
        return bool(
            self.cloudinary_cloud_name.strip()
            and self.cloudinary_api_key.strip()
            and self.cloudinary_api_secret.strip()
        )

    # Google OAuth (Sign in with Google — web + native app). Empty by default:
    # the routes exist and respond 503 "not configured" instead of 404 until
    # real credentials are set (Google Cloud Console → OAuth client ID, Web
    # application type — used for BOTH web and app, since the app opens the
    # same browser-based consent screen via ASWebAuthenticationSession).
    google_client_id: str = ""
    google_client_secret: str = ""
    # Leave empty to auto-derive as {public_base_url}/api/auth/google/callback.
    google_redirect_uri: str = ""

    @property
    def google_enabled(self) -> bool:
        return bool(self.google_client_id.strip() and self.google_client_secret.strip())

    @property
    def google_redirect_uri_effective(self) -> str:
        return self.google_redirect_uri.strip() or f"{self.public_base_url}/api/auth/google/callback"

    # Sign in with Apple. Unlike Google/Boostyfi this needs NO client secret
    # on our side — the app talks to Apple directly and hands us a signed
    # identity_token (JWT) that we verify against Apple's public JWKS. We
    # only need to know our own audience(s) to check the `aud` claim: the
    # iOS app's Bundle ID (native flow) and/or a Services ID (web/Android
    # "Sign in with Apple" button, if ever added). Set at least one to enable.
    apple_bundle_id: str = ""
    apple_service_id: str = ""

    @property
    def apple_enabled(self) -> bool:
        return bool(self.apple_bundle_id.strip() or self.apple_service_id.strip())

    @property
    def apple_audiences(self) -> list[str]:
        return [a for a in (self.apple_bundle_id.strip(), self.apple_service_id.strip()) if a]

    # Native-app custom URL scheme. After a browser-based OAuth flow (Google)
    # started with ?platform=app, the callback redirects here instead of the
    # web frontend so the app (ASWebAuthenticationSession) can capture the
    # session token from the URL.
    app_deep_link_scheme: str = "arteki"

    # Boostyfi (OAuth + Wallet)
    boostify_mock: bool = True
    boostify_base_url: str = "https://api.boostyfi.com/api/v1"
    boostify_client_id: str = "arteki"
    boostify_client_secret: str = "change-me"
    boostify_redirect_uri: str = "http://localhost:8000/api/auth/boostify/callback"
    boostify_webhook_secret: str = "change-me-too"

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def session_ttl_seconds(self) -> int:
        return self.session_ttl_days * 24 * 60 * 60

    @property
    def magic_link_ttl_seconds(self) -> int:
        return self.magic_link_ttl_minutes * 60

    @property
    def password_reset_ttl_seconds(self) -> int:
        return self.password_reset_ttl_minutes * 60


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
