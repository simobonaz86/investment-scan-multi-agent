from __future__ import annotations

from typing import Any

from invest_scan.agents.market_data_agent import MarketDataAgent
from invest_scan.settings import Settings
from invest_scan.ttl_cache import TTLCache
from invest_scan.services.universe_service import UniverseService


class RankingService:
    def __init__(self, *, settings: Settings, http, universe: UniverseService) -> None:
        self._settings = settings
        self._market = MarketDataAgent(http, finnhub_api_key=settings.finnhub_api_key)
        self._universe = universe
        self._cache: TTLCache[str, dict[str, Any]] = TTLCache(ttl_seconds=3600)

    async def sp500_weekly(self, *, max_tickers: int = 200) -> dict[str, Any]:
        key = f"sp500_weekly:{max_tickers}"
        cached = self._cache.get(key)
        if cached is not None:
            return cached

        uni = await self._universe.get_universe()
        tickers = (uni.get("tickers") or [])[:max_tickers]
        if not tickers:
            result = {"universe_size": 0, "items": []}
            self._cache.set(key, result)
            return result

        try:
            histories, source = await self._market.fetch_histories(
                tickers, period="30d", attempts=2, backoff_seconds=1.0
            )
        except Exception:
            histories, source = {}, "none"

        scored: list[tuple[str, float]] = []
        for t in tickers:
            pts = histories.get(str(t).strip().upper()) or []
            if len(pts) < 6:
                continue
            closes = [p.close for p in pts]
            try:
                market = self._market._analyze_from_ohlcv(  # noqa: SLF001
                    t,
                    source=source,
                    closes=closes,
                    highs=[p.high for p in pts],
                    lows=[p.low for p in pts],
                    volumes=[p.volume for p in pts],
                )
                r1w = market.get("return_1w")
                if isinstance(r1w, (int, float)):
                    scored.append((t, float(r1w)))
            except Exception:
                continue

        scored.sort(key=lambda x: x[1], reverse=True)

        items: list[dict[str, Any]] = []
        for idx, (t, r) in enumerate(scored, start=1):
            items.append({"ticker": t, "return_1w": r, "rank": idx})

        result = {"universe_size": len(tickers), "scored_size": len(scored), "items": items}
        self._cache.set(key, result)
        return result

