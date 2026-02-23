from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="INVEST_SCAN_", extra="ignore")

    db_path: str = "data/app.db"
    http_timeout_seconds: float = 12.0
    max_concurrent_fetches: int = 12
    cache_ttl_seconds: int = 300
    max_news_items: int = 8

    # Automated scanning (MVP scheduler)
    autoscan_enabled: bool = False
    autoscan_interval_seconds: int = 300
    autoscan_tickers_csv: str = "AAPL,MSFT"
    autoscan_only_market_hours: bool = True
    market_timezone: str = "America/New_York"
    market_open_hhmm: str = "09:30"
    market_close_hhmm: str = "16:00"


settings = Settings()

