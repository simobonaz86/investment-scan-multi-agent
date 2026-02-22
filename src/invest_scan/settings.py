from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="INVEST_SCAN_", extra="ignore")

    db_path: str = "data/app.db"
    http_timeout_seconds: float = 12.0
    max_concurrent_fetches: int = 12
    cache_ttl_seconds: int = 300
    max_news_items: int = 8


settings = Settings()

