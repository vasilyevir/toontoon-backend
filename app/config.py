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

    # Redis
    redis_url: str = "redis://localhost:6379/0"
    use_fake_redis: bool = False

    # Sessions
    session_cookie_name: str = "arteki-session"
    session_ttl_days: int = 30
    magic_link_ttl_minutes: int = 15
    session_cookie_samesite: str = "lax"
    session_cookie_secure: bool = False
    signup_teki_balance: int = 3

    # OpenAI
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"

    @property
    def openai_enabled(self) -> bool:
        return bool(self.openai_api_key.strip())

    # Generation
    rate_limit_per_hour: int = 30
    image_teki_cost: int = 1
    video_teki_cost: int = 2
    pollinations_image_url: str = "https://image.pollinations.ai"
    pollinations_text_url: str = "https://text.pollinations.ai"
    vision_timeout_seconds: int = 7

    # Boostify
    boostify_mock: bool = True
    boostify_base_url: str = "https://api.boostify.example"
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


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
