from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "OpenAI AI Platform"
    environment: str = "development"
    database_url: str = "postgresql+asyncpg://platform:platform@localhost:5432/platform"
    redis_url: str = "redis://localhost:6379/0"
    openai_api_key: str = ""
    openai_model: str = "gpt-5.2"
    jwt_secret_key: str = "change-this-in-production"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 30
    cache_ttl_seconds: int = 300
    cache_key_version: str = "infra-agents-v2"
    rate_limit_window_seconds: int = 60
    rate_limit_requests: int = 30
    agent_max_turns: int = 6
    agent_timeout_seconds: int = 90
    cors_origins: str = "http://localhost:3000,http://localhost:8000"
    otel_service_name: str = "openai-ai-platform"
    otel_exporter_otlp_endpoint: str | None = None
    db_pool_size: int = 10
    db_max_overflow: int = 20

    model_config = SettingsConfigDict(
        env_file=None,
        env_prefix="",
        case_sensitive=False,
        extra="ignore",
    )

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    def validate_runtime(self) -> None:
        if self.environment.lower() in {"production", "prod"}:
            if len(self.jwt_secret_key) < 32 or self.jwt_secret_key == "change-this-in-production":
                raise RuntimeError("JWT_SECRET_KEY must be a strong secret in production")
            if not self.openai_api_key:
                raise RuntimeError("OPENAI_API_KEY is required in production")


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.validate_runtime()
    return settings
