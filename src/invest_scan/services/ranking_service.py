from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import httpx

from invest_scan.agents.market_data_agent import MarketDataAgent
from invest_scan.settings import Settings
from invest_scan.ttl_cache import TTLCache


def _read_universe(path: str, *, max_tickers: int) -> list[str]:
    p = Path(path)
    if not p.exists():
        return []
    tickers: list[str] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        t = line.strip().upper()
        if not t or t.startswith("#"):
            continue
        tickers.append(t)
        if len(tickers) >= max_tickers:
            break
    # de-dupe preserving order
    return list(dict.fromkeys(tickers))


class RankingService:
    def __init__(self, *, settings: Settings, http: httpx.AsyncClient) -> None:
        self._settings = settings
        self._http = http
        self._market = MarketDataAgent(http)
        self._sem = asyncio.Semaphore(settings.max_concurrent_fetches)
        self._cache: TTLCache[str, dict[str, Any]] = TTLCache(ttl_seconds=3600)

    async def _limited(self, coro):
        async with self._sem:
            return await coro

    async def sp500_weekly(self, *, universe_path: str, max_tickers: int = 200) -> dict[str, Any]:
        key = f"sp500_weekly:{universe_path}:{max_tickers}"
        cached = self._cache.get(key)
        if cached is not None:
            return cached

        tickers = _read_universe(universe_path, max_tickers=max_tickers)
        if not tickers:
            result = {"universe_size": 0, "items": []}
            self._cache.set(key, result)
            return result

        async def one(t: str) -> tuple[str, float | None]:
            try:
                market = await self._limited(self._market.analyze(t))
                r1w = market.get("return_1w")
                return t, (float(r1w) if isinstance(r1w, (int, float)) else None)
            except httpx.HTTPError:
                return t, None
            except Exception:
                return t, None

        pairs = await asyncio.gather(*(one(t) for t in tickers))
        scored = [(t, r) for (t, r) in pairs if r is not None]
        scored.sort(key=lambda x: x[1], reverse=True)

        items: list[dict[str, Any]] = []
        for idx, (t, r) in enumerate(scored, start=1):
            items.append({"ticker": t, "return_1w": r, "rank": idx})

        result = {"universe_size": len(tickers), "scored_size": len(scored), "items": items}
        self._cache.set(key, result)
        return result

