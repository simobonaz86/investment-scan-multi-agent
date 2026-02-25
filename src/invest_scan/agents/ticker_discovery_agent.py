from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from invest_scan.settings import Settings
from invest_scan.symbols import normalize_yahoo_symbol


class TickerDiscoveryAgent:
    """
    Finds a subset of tickers to scan (discovery) from a larger configured universe.

    Primary strategy: pull symbols from Yahoo Finance predefined screeners, then intersect with the
    configured base universe (e.g. S&P 500 tickers).
    """

    def __init__(self, *, http: httpx.AsyncClient, settings: Settings) -> None:
        self._http = http
        self._settings = settings
        self._log = logging.getLogger(__name__)

    def _screener_ids(self) -> list[str]:
        raw = str(self._settings.ticker_discovery_screener_ids_csv or "").strip()
        ids = [x.strip() for x in raw.split(",") if x.strip()]
        return ids or ["most_actives"]

    async def _fetch_screener_symbols(self, screener_id: str) -> list[str]:
        # Yahoo Finance predefined screener endpoint.
        url = "https://query2.finance.yahoo.com/v1/finance/screener/predefined/saved"
        count = int(max(25, min(250, self._settings.ticker_discovery_count_per_screener)))
        try:
            resp = await self._http.get(
                url,
                params={"scrIds": screener_id, "count": count, "start": 0},
                headers={"accept": "application/json"},
            )
            resp.raise_for_status()
        except Exception as e:
            self._log.warning("Ticker discovery: screener fetch failed (%s): %s", screener_id, e)
            return []

        try:
            payload = resp.json()
        except Exception:
            return []

        quotes: list[dict[str, Any]] = []
        try:
            finance = payload.get("finance") if isinstance(payload, dict) else None
            results = finance.get("result") if isinstance(finance, dict) else None
            if isinstance(results, list) and results:
                quotes = results[0].get("quotes") or []
        except Exception:
            quotes = []

        out: list[str] = []
        for q in quotes:
            if not isinstance(q, dict):
                continue
            sym = q.get("symbol")
            if not sym:
                continue
            n = normalize_yahoo_symbol(str(sym))
            if n:
                out.append(n)
        return list(dict.fromkeys(out))

    async def discover(self, *, base_tickers: list[str], max_tickers: int) -> dict[str, Any]:
        base = [normalize_yahoo_symbol(t) for t in (base_tickers or [])]
        base = [t for t in base if t]
        base_set = set(base)
        want = int(max(1, max_tickers))

        if not self._settings.ticker_discovery_enabled:
            return {
                "enabled": False,
                "strategy": "disabled",
                "base_universe_size": len(base),
                "discovered_size": min(len(base), want),
                "tickers": base[:want],
            }

        t0 = time.perf_counter()
        ids = self._screener_ids()
        raw: list[str] = []
        for sid in ids:
            raw.extend(await self._fetch_screener_symbols(sid))
        raw = list(dict.fromkeys(raw))

        discovered = [t for t in raw if t in base_set]
        # If Yahoo screeners return nothing (blocked), fall back to the base universe list.
        if not discovered:
            discovered = base[:want]
            strategy = "base_universe_fallback"
        else:
            discovered = discovered[:want]
            strategy = "yahoo_screeners_intersect_universe"

        dt = time.perf_counter() - t0
        self._log.info(
            "Ticker discovery completed: %d discovered (raw=%d) in %.2fs via %s",
            len(discovered),
            len(raw),
            dt,
            ",".join(ids),
        )
        return {
            "enabled": True,
            "strategy": strategy,
            "screeners": ids,
            "base_universe_size": len(base),
            "raw_size": len(raw),
            "discovered_size": len(discovered),
            "tickers": discovered,
        }

