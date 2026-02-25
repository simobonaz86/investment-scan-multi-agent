from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

import httpx

from invest_scan import db
from invest_scan.agents import MarketDataAgent, NewsAgent, RiskAgent, SignalsAgent, SummaryAgent
from invest_scan.settings import Settings
from invest_scan.services.portfolio_service import PortfolioService
from invest_scan.ttl_cache import TTLCache


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ScanService:
    def __init__(
        self,
        *,
        settings: Settings,
        http: httpx.AsyncClient,
        portfolio_service: PortfolioService | None = None,
    ) -> None:
        self._settings = settings
        self._http = http
        self._portfolio = portfolio_service
        self._log = logging.getLogger(__name__)

        self._sem = asyncio.Semaphore(settings.max_concurrent_fetches)
        self._market = MarketDataAgent(http, finnhub_api_key=settings.finnhub_api_key)
        self._news = NewsAgent(http, max_items=settings.max_news_items)
        self._signals = SignalsAgent()
        self._risk = RiskAgent()
        self._summary = SummaryAgent()

        self._market_cache: TTLCache[str, tuple[dict[str, Any], dict[str, list[float]]]] = TTLCache(
            ttl_seconds=settings.cache_ttl_seconds
        )
        self._news_cache: TTLCache[str, dict[str, Any]] = TTLCache(ttl_seconds=settings.cache_ttl_seconds)

    async def _limited(self, coro):
        async with self._sem:
            return await coro

    async def _get_market(self, ticker: str) -> tuple[dict[str, Any], dict[str, list[float]]]:
        cached = self._market_cache.get(ticker)
        if cached is not None:
            return cached
        market, series = await self._limited(self._market.fetch_and_analyze(ticker))
        self._market_cache.set(ticker, (market, series))
        return market, series

    async def _get_news(self, ticker: str) -> dict[str, Any]:
        key = f"{ticker}:stock"
        cached = self._news_cache.get(key)
        if cached is not None:
            return cached
        news = await self._limited(self._news.fetch(f"{ticker} stock"))
        self._news_cache.set(key, news)
        return news

    async def scan_once(self, request: dict[str, Any]) -> dict[str, Any]:
        t0 = datetime.now(timezone.utc)
        tickers = request.get("tickers") or []
        tickers = [str(t).strip().upper() for t in tickers if str(t).strip()]
        tickers = list(dict.fromkeys(tickers))[:30]

        cash_usd: float | None = None
        if self._portfolio is not None:
            try:
                cash_usd = (await self._portfolio.get_portfolio()).cash_usd
            except Exception:
                cash_usd = None

        # Batch fetch market data once (avoids N separate downloads).
        histories, source = await self._market.fetch_histories(
            tickers, period="120d", attempts=2, backoff_seconds=1.0
        )
        market_by_ticker: dict[str, dict[str, Any]] = {}
        series_by_ticker: dict[str, dict[str, list[float]]] = {}
        for t in tickers:
            pts = histories.get(str(t).strip().upper()) or []
            closes = [p.close for p in pts]
            highs = [p.high for p in pts]
            lows = [p.low for p in pts]
            vols = [p.volume for p in pts]
            market = self._market._analyze_from_ohlcv(  # noqa: SLF001
                t, source=source, closes=closes, highs=highs, lows=lows, volumes=vols
            )
            market_by_ticker[t] = market
            series_by_ticker[t] = {"closes": closes, "highs": highs, "lows": lows, "volumes": vols}

        async def one(ticker: str) -> dict[str, Any]:
            try:
                news_task = asyncio.create_task(self._get_news(ticker))

                market = market_by_ticker.get(ticker) or {"ticker": ticker, "source": source, "error": "no_history"}
                if market.get("error"):
                    hint = str(market.get("error"))
                    if hint in {"no_history", "insufficient_history"}:
                        hint = (
                            f"{hint} (if non-US ticker, use Yahoo suffix like VUAA.L / VUAA.MI / 5J50.DE)"
                        )
                    return {"ticker": ticker, "error": hint}

                series = series_by_ticker.get(ticker) or {"closes": [], "highs": [], "lows": [], "volumes": []}
                closes = series.get("closes") or []
                signals = self._signals.analyze(closes, market=market)
                risk = self._risk.score(volatility_60d_ann=market.get("volatility_60d_ann"))
                news = await news_task
                trade_plan = self._risk.plan_trade(
                    cash_usd=cash_usd,
                    entry_price=market.get("last_close"),
                    atr14=market.get("atr14"),
                    risk_per_trade_pct=self._settings.risk_per_trade_pct,
                    stop_atr_multiple=self._settings.stop_atr_multiple,
                    min_position_usd=self._settings.min_position_usd,
                )
                report = {
                    "ticker": ticker,
                    "market": market,
                    "signals": signals,
                    "risk": risk,
                    "trade_plan": trade_plan,
                    "news": news,
                }
                report["summary"] = self._summary.summarize(report)
                return report
            except httpx.HTTPError as e:
                return {"ticker": ticker, "error": f"http_error: {e.__class__.__name__}"}
            except Exception as e:
                return {"ticker": ticker, "error": f"unexpected_error: {e.__class__.__name__}"}

        reports = await asyncio.gather(*(one(t) for t in tickers))
        failed = sum(1 for r in reports if r.get("error"))
        self._log.info(
            "Scan completed: %d tickers in %.2fs, %d failed",
            len(tickers),
            (datetime.now(timezone.utc) - t0).total_seconds(),
            failed,
        )

        return {
            "generated_at": _utcnow_iso(),
            "tickers": tickers,
            "reports": reports,
        }

    async def run_and_persist(self, *, scan_id: UUID, request: dict[str, Any]) -> None:
        await db.mark_running(self._settings.db_path, scan_id)
        try:
            result = await self.scan_once(request)
            await db.set_result(self._settings.db_path, scan_id, result)
        except Exception as e:
            await db.set_failed(self._settings.db_path, scan_id, f"{e.__class__.__name__}: {e}")


def scan_record_from_row(row: dict[str, Any]) -> dict[str, Any]:
    def parse_dt(x: str | None) -> str | None:
        return x

    return {
        "scan_id": row["scan_id"],
        "created_at": parse_dt(row["created_at"]),
        "status": row["status"],
        "started_at": parse_dt(row.get("started_at")),
        "finished_at": parse_dt(row.get("finished_at")),
        "request": json.loads(row["request_json"]),
        "result": json.loads(row["result_json"]) if row.get("result_json") else None,
        "error": row.get("error"),
    }

