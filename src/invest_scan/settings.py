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

    # Optional: S&P 500 weekly ranking (expensive; keep off by default)
    sp500_weekly_ranking_enabled: bool = False
    sp500_universe_path: str = "data/sp500_tickers.txt"
    sp500_ranking_max_tickers: int = 200

    # Position sizing (MVP)
    risk_per_trade_pct: float = 0.01
    stop_atr_multiple: float = 2.0
    min_position_usd: float = 100.0


settings = Settings()

