from __future__ import annotations

import csv
import io
from datetime import datetime, timezone
from typing import Any

import httpx

from invest_scan.settings import Settings
from invest_scan.symbols import normalize_yahoo_symbol
from invest_scan.ttl_cache import TTLCache


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class UniverseService:
    def __init__(self, *, settings: Settings, http: httpx.AsyncClient) -> None:
        self._settings = settings
        self._http = http
        self._cache: TTLCache[str, dict[str, Any]] = TTLCache(ttl_seconds=settings.universe_refresh_seconds)

    async def get_universe(self) -> dict[str, Any]:
        key = f"universe:{self._settings.universe_source}"
        if self._settings.universe_source == "yahoo_screener":
            key = (
                f"universe:yahoo_screener:{self._settings.universe_yahoo_screener_id}"
                f":{self._settings.universe_yahoo_screener_count}"
                f":{self._settings.universe_max_tickers}"
            )
        cached = self._cache.get(key)
        if cached is not None:
            return cached

        if self._settings.universe_source == "sp500_datahub_csv":
            resp = await self._http.get(self._settings.universe_datahub_csv_url)
            resp.raise_for_status()
            text = resp.text
            reader = csv.DictReader(io.StringIO(text))
            tickers: list[str] = []
            for row in reader:
                sym = (row.get("Symbol") or row.get("symbol") or "").strip().upper()
                if not sym:
                    continue
                n = normalize_yahoo_symbol(sym)
                if n:
                    tickers.append(n)
                if len(tickers) >= int(self._settings.universe_max_tickers):
                    break
            tickers = list(dict.fromkeys(tickers))
            result = {"source": self._settings.universe_source, "fetched_at": _utcnow_iso(), "tickers": tickers}
            self._cache.set(key, result)
            return result

        if self._settings.universe_source == "yahoo_screener":
            # Fetch symbol list directly from Yahoo Finance predefined screeners.
            # Example screener IDs: most_actives, day_gainers, day_losers.
            url = "https://query2.finance.yahoo.com/v1/finance/screener/predefined/saved"
            scr_id = str(self._settings.universe_yahoo_screener_id or "most_actives").strip()
            page_size = int(max(25, min(250, self._settings.universe_yahoo_screener_count)))
            want = int(max(1, self._settings.universe_max_tickers))
            tickers: list[str] = []
            start = 0
            while len(tickers) < want and start < 5000:
                resp = await self._http.get(
                    url,
                    params={"scrIds": scr_id, "count": page_size, "start": start},
                    headers={"accept": "application/json"},
                )
                resp.raise_for_status()
                payload = resp.json()
                quotes: list[dict[str, Any]] = []
                try:
                    finance = payload.get("finance") if isinstance(payload, dict) else None
                    results = finance.get("result") if isinstance(finance, dict) else None
                    if isinstance(results, list) and results:
                        quotes = results[0].get("quotes") or []
                except Exception:
                    quotes = []

                got = 0
                for q in quotes:
                    if not isinstance(q, dict):
                        continue
                    sym = q.get("symbol")
                    if not sym:
                        continue
                    n = normalize_yahoo_symbol(str(sym))
                    if n:
                        tickers.append(n)
                        got += 1
                    if len(tickers) >= want:
                        break

                if got == 0 or len(quotes) < page_size:
                    break
                start += page_size

            tickers = list(dict.fromkeys(tickers))[:want]
            result = {
                "source": self._settings.universe_source,
                "fetched_at": _utcnow_iso(),
                "screener_id": scr_id,
                "tickers": tickers,
            }
            self._cache.set(key, result)
            return result

        result = {"source": self._settings.universe_source, "fetched_at": _utcnow_iso(), "tickers": []}
        self._cache.set(key, result)
        return result

