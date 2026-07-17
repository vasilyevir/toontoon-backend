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

    # Redis
    redis_url: str = "redis://localhost:6379/0"
    use_fake_redis: bool = False

    # Sessions
    session_cookie_name: str = "arteki-session"
    session_ttl_days: int = 30
    magic_link_ttl_minutes: int = 15
    password_reset_ttl_minutes: int = 60
    session_cookie_samesite: str = "lax"
    session_cookie_secure: bool = False
    signup_teki_balance: int = 30

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

    # Video generation (kie.ai / ByteDance Seedance)
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
