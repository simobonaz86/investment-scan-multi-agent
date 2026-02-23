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
    open: float
    high: float
    low: float
    close: float
    volume: float


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


def _safe_int(x: str) -> float | None:
    try:
        v = int(float(x))
        return float(v)
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


def _atr(highs: list[float], lows: list[float], closes: list[float], period: int = 14) -> float | None:
    if len(closes) < period + 1 or len(highs) != len(lows) or len(lows) != len(closes):
        return None
    trs: list[float] = []
    for i in range(1, len(closes)):
        h = highs[i]
        lo = lows[i]
        prev = closes[i - 1]
        tr = max(h - lo, abs(h - prev), abs(lo - prev))
        trs.append(tr)
    if len(trs) < period:
        return None
    xs = trs[-period:]
    return sum(xs) / len(xs)


class MarketDataAgent:
    def __init__(self, http: httpx.AsyncClient) -> None:
        self._http = http

    def _analyze_from_ohlcv(
        self,
        ticker: str,
        *,
        closes: list[float],
        highs: list[float],
        lows: list[float],
        volumes: list[float],
    ) -> dict[str, Any]:
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

        last_vol = volumes[-1] if volumes else None
        vol_avg_20 = (sum(volumes[-20:]) / len(volumes[-20:])) if len(volumes) >= 5 else None
        vol_spike_ratio = (last_vol / vol_avg_20) if (last_vol and vol_avg_20 and vol_avg_20 > 0) else None
        vol_spike = bool(vol_spike_ratio is not None and vol_spike_ratio >= 2.0)

        atr14 = _atr(highs, lows, closes, period=14)

        return {
            "ticker": ticker,
            "source": "stooq",
            "last_close": last,
            "prev_close": prev,
            "day_return": day_return,
            "return_1w": _window_return(closes, 5),
            "return_1m": _window_return(closes, 21),
            "return_3m": _window_return(closes, 63),
            "return_1y": _window_return(closes, 252),
            "volatility_60d_ann": vol_60d,
            "atr14": atr14,
            "last_volume": last_vol,
            "volume_avg_20d": vol_avg_20,
            "volume_spike_ratio": vol_spike_ratio,
            "volume_spike": vol_spike,
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
            o = row.get("Open")
            h = row.get("High")
            lo = row.get("Low")
            c = row.get("Close")
            v = row.get("Volume")
            if not d or not c or not o or not h or not lo:
                continue
            open_ = _safe_float(o)
            high = _safe_float(h)
            low = _safe_float(lo)
            close = _safe_float(c)
            vol = _safe_int(v) if v is not None else 0.0
            if open_ is None or high is None or low is None or close is None or vol is None:
                continue
            try:
                y, m, dd = d.split("-")
                points.append(
                    MarketHistoryPoint(
                        day=date(int(y), int(m), int(dd)),
                        open=open_,
                        high=high,
                        low=low,
                        close=close,
                        volume=vol,
                    )
                )
            except Exception:
                continue

        points.sort(key=lambda p: p.day)
        return points

    async def analyze(self, ticker: str) -> dict[str, Any]:
        history = await self.fetch_history(ticker)
        closes = [p.close for p in history]
        highs = [p.high for p in history]
        lows = [p.low for p in history]
        vols = [p.volume for p in history]
        return self._analyze_from_ohlcv(ticker, closes=closes, highs=highs, lows=lows, volumes=vols)

    async def fetch_and_analyze(
        self, ticker: str
    ) -> tuple[dict[str, Any], dict[str, list[float]]]:
        history = await self.fetch_history(ticker)
        closes = [p.close for p in history]
        highs = [p.high for p in history]
        lows = [p.low for p in history]
        vols = [p.volume for p in history]
        market = self._analyze_from_ohlcv(ticker, closes=closes, highs=highs, lows=lows, volumes=vols)
        series = {"closes": closes, "highs": highs, "lows": lows, "volumes": vols}
        return market, series

