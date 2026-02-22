from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

import httpx

from invest_scan import db
from invest_scan.agents import MarketDataAgent, NewsAgent, RiskAgent, SignalsAgent, SummaryAgent
from invest_scan.settings import Settings
from invest_scan.ttl_cache import TTLCache


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ScanService:
    def __init__(self, *, settings: Settings, http: httpx.AsyncClient) -> None:
        self._settings = settings
        self._http = http

        self._sem = asyncio.Semaphore(settings.max_concurrent_fetches)
        self._market = MarketDataAgent(http)
        self._news = NewsAgent(http, max_items=settings.max_news_items)
        self._signals = SignalsAgent()
        self._risk = RiskAgent()
        self._summary = SummaryAgent()

        self._market_cache: TTLCache[str, tuple[dict[str, Any], list[float]]] = TTLCache(
            ttl_seconds=settings.cache_ttl_seconds
        )
        self._news_cache: TTLCache[str, dict[str, Any]] = TTLCache(ttl_seconds=settings.cache_ttl_seconds)

    async def _limited(self, coro):
        async with self._sem:
            return await coro

    async def _get_market(self, ticker: str) -> tuple[dict[str, Any], list[float]]:
        cached = self._market_cache.get(ticker)
        if cached is not None:
            return cached
        market, closes = await self._limited(self._market.fetch_and_analyze(ticker))
        self._market_cache.set(ticker, (market, closes))
        return market, closes

    async def _get_news(self, ticker: str) -> dict[str, Any]:
        key = f"{ticker}:stock"
        cached = self._news_cache.get(key)
        if cached is not None:
            return cached
        news = await self._limited(self._news.fetch(f"{ticker} stock"))
        self._news_cache.set(key, news)
        return news

    async def scan_once(self, request: dict[str, Any]) -> dict[str, Any]:
        tickers = request.get("tickers") or []
        tickers = [str(t).strip().upper() for t in tickers if str(t).strip()]
        tickers = list(dict.fromkeys(tickers))[:30]

        async def one(ticker: str) -> dict[str, Any]:
            try:
                market, closes = await self._get_market(ticker)
                signals = self._signals.analyze(closes)
                risk = self._risk.score(volatility_60d_ann=market.get("volatility_60d_ann"))
                news = await self._get_news(ticker)
                report = {
                    "ticker": ticker,
                    "market": market,
                    "signals": signals,
                    "risk": risk,
                    "news": news,
                }
                report["summary"] = self._summary.summarize(report)
                return report
            except httpx.HTTPError as e:
                return {"ticker": ticker, "error": f"http_error: {e.__class__.__name__}"}
            except Exception as e:
                return {"ticker": ticker, "error": f"unexpected_error: {e.__class__.__name__}"}

        reports = await asyncio.gather(*(one(t) for t in tickers))

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

