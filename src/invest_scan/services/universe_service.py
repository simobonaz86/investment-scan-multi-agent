from __future__ import annotations

import csv
import io
from datetime import datetime, timezone
from typing import Any

import httpx

from invest_scan.settings import Settings
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
                tickers.append(sym.replace(".", "-"))  # BRK.B -> BRK-B (common convention)
                if len(tickers) >= int(self._settings.universe_max_tickers):
                    break
            tickers = list(dict.fromkeys(tickers))
            result = {"source": self._settings.universe_source, "fetched_at": _utcnow_iso(), "tickers": tickers}
            self._cache.set(key, result)
            return result

        result = {"source": self._settings.universe_source, "fetched_at": _utcnow_iso(), "tickers": []}
        self._cache.set(key, result)
        return result

