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

    # Universe + rankings
    universe_source: str = "sp500_datahub_csv"
    universe_datahub_csv_url: str = "https://datahub.io/core/s-and-p-500-companies/r/constituents.csv"
    universe_refresh_seconds: int = 86400
    universe_max_tickers: int = 500
    universe_yahoo_screener_id: str = "most_actives"
    universe_yahoo_screener_count: int = 250

    # Ticker discovery (build a dynamic subset of the universe to scan)
    ticker_discovery_enabled: bool = True
    ticker_discovery_screener_ids_csv: str = "most_actives,day_gainers"
    ticker_discovery_count_per_screener: int = 250
    ticker_discovery_max_tickers: int = 200

    # Ranking: rolling 1-week return (5 trading days), can run any day.
    sp500_weekly_ranking_enabled: bool = True
    sp500_ranking_max_tickers: int = 200

    # Market-wide scan (find candidates automatically)
    marketscan_enabled: bool = True
    marketscan_interval_seconds: int = 1800
    marketscan_only_market_hours: bool = True
    marketscan_top_n: int = 25
    marketscan_min_score: float = 5.0

    # Position sizing (MVP)
    risk_per_trade_pct: float = 0.01
    stop_atr_multiple: float = 2.0
    min_position_usd: float = 100.0

    # Journal / P&L baseline
    initial_budget: float = 1000.0

    # Recommendations
    rec_expiry_hours: int = 4

    # Optional market data fallback
    finnhub_api_key: str = ""

    # Intraday stage (watchlist triggers)
    intraday_enabled: bool = True
    intraday_only_market_hours: bool = True
    intraday_interval: str = "15m"  # yfinance interval (e.g., 5m/15m/30m/60m)
    intraday_period: str = "5d"  # yfinance period (intraday lookback)
    intraday_watchlist_size: int = 20
    intraday_poll_seconds: int = 180

    # Short-horizon portfolio (tactical sleeve)
    total_portfolio_usd: float = 0.0
    tactical_sleeve_pct: float = 0.01  # start at 1% until confidence increases
    tactical_max_positions: int = 4
    tactical_risk_per_trade_pct: float = 0.01  # of sleeve value
    tactical_max_position_pct: float = 0.35  # of sleeve value per position


settings = Settings()

