from __future__ import annotations

import csv
import io
import math
from dataclasses import dataclass
from datetime import date
from typing import Any

import httpx


@dataclass(frozen=True)
class MarketHistoryPoint:
    day: date
    close: float


def _to_stooq_symbol(ticker: str) -> str:
    t = ticker.strip().lower()
    if not t:
        return t
    if "." in t:
        return t
    return f"{t}.us"


def _safe_float(x: str) -> float | None:
    try:
        v = float(x)
        if math.isnan(v) or math.isinf(v):
            return None
        return v
    except Exception:
        return None


def _stdev(xs: list[float]) -> float | None:
    if len(xs) < 2:
        return None
    mean = sum(xs) / len(xs)
    var = sum((x - mean) ** 2 for x in xs) / (len(xs) - 1)
    return math.sqrt(var)


def _annualized_volatility(daily_returns: list[float]) -> float | None:
    sd = _stdev(daily_returns)
    if sd is None:
        return None
    return sd * math.sqrt(252.0)


def _window_return(closes: list[float], trading_days: int) -> float | None:
    if len(closes) <= trading_days:
        return None
    start = closes[-(trading_days + 1)]
    end = closes[-1]
    if start <= 0:
        return None
    return (end / start) - 1.0


class MarketDataAgent:
    def __init__(self, http: httpx.AsyncClient) -> None:
        self._http = http

    def _analyze_from_closes(self, ticker: str, closes: list[float]) -> dict[str, Any]:
        if len(closes) < 2:
            return {"ticker": ticker, "source": "stooq", "error": "insufficient_history"}

        last = closes[-1]
        prev = closes[-2]
        day_return = (last / prev - 1.0) if prev > 0 else None

        daily_returns: list[float] = []
        for i in range(1, len(closes)):
            if closes[i - 1] <= 0:
                continue
            daily_returns.append(closes[i] / closes[i - 1] - 1.0)

        vol_60d = _annualized_volatility(daily_returns[-60:])

        return {
            "ticker": ticker,
            "source": "stooq",
            "last_close": last,
            "prev_close": prev,
            "day_return": day_return,
            "return_1m": _window_return(closes, 21),
            "return_3m": _window_return(closes, 63),
            "return_1y": _window_return(closes, 252),
            "volatility_60d_ann": vol_60d,
            "history_days": len(closes),
        }

    async def fetch_history(self, ticker: str) -> list[MarketHistoryPoint]:
        sym = _to_stooq_symbol(ticker)
        if not sym:
            return []

        url = "https://stooq.com/q/d/l/"
        params = {"s": sym, "i": "d"}
        resp = await self._http.get(url, params=params)
        resp.raise_for_status()

        text = resp.text.strip()
        if not text or text.lower().startswith("error"):
            return []

        reader = csv.DictReader(io.StringIO(text))
        points: list[MarketHistoryPoint] = []
        for row in reader:
            d = row.get("Date")
            c = row.get("Close")
            if not d or not c:
                continue
            close = _safe_float(c)
            if close is None:
                continue
            try:
                y, m, dd = d.split("-")
                points.append(MarketHistoryPoint(day=date(int(y), int(m), int(dd)), close=close))
            except Exception:
                continue

        points.sort(key=lambda p: p.day)
        return points

    async def analyze(self, ticker: str) -> dict[str, Any]:
        history = await self.fetch_history(ticker)
        closes = [p.close for p in history if p.close is not None]
        return self._analyze_from_closes(ticker, closes)

    async def fetch_and_analyze(self, ticker: str) -> tuple[dict[str, Any], list[float]]:
        history = await self.fetch_history(ticker)
        closes = [p.close for p in history if p.close is not None]
        return self._analyze_from_closes(ticker, closes), closes

