from __future__ import annotations

import asyncio
import logging
import math
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any

import httpx
import yfinance as yf


@dataclass(frozen=True)
class MarketHistoryPoint:
    day: date
    open: float
    high: float
    low: float
    close: float
    volume: float


def _to_yahoo_symbol(ticker: str) -> str:
    # Yahoo uses dashes for class shares like BRK-B (not BRK.B).
    t = ticker.strip().upper()
    if not t:
        return t
    return t.replace(".", "-")


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
    def __init__(self, http: httpx.AsyncClient, *, finnhub_api_key: str = "") -> None:
        self._http = http
        self._finnhub_api_key = str(finnhub_api_key or "").strip()
        self._log = logging.getLogger(__name__)

    def _analyze_from_ohlcv(
        self,
        ticker: str,
        *,
        source: str,
        closes: list[float],
        highs: list[float],
        lows: list[float],
        volumes: list[float],
    ) -> dict[str, Any]:
        if len(closes) < 2:
            return {"ticker": ticker, "source": source, "error": "insufficient_history"}

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
            "source": source,
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

    async def _fetch_history_yahoo(self, ticker: str) -> list[MarketHistoryPoint]:
        sym = _to_yahoo_symbol(ticker)
        if not sym:
            return []

        # No-crumb Yahoo endpoint.
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
        timeout = httpx.Timeout(8.0, connect=3.0)
        resp = await self._http.get(
            url,
            params={
                "range": "1y",
                "interval": "1d",
                "includePrePost": "false",
                "events": "div|split",
            },
            timeout=timeout,
        )
        resp.raise_for_status()
        payload = resp.json()
        chart = payload.get("chart") if isinstance(payload, dict) else None
        if not isinstance(chart, dict) or chart.get("error"):
            return []
        result = chart.get("result")
        if not isinstance(result, list) or not result:
            return []
        r0 = result[0] if isinstance(result[0], dict) else None
        if not isinstance(r0, dict):
            return []

        timestamps = r0.get("timestamp")
        indicators = r0.get("indicators")
        if not isinstance(timestamps, list) or not isinstance(indicators, dict):
            return []
        quote = indicators.get("quote")
        if not isinstance(quote, list) or not quote or not isinstance(quote[0], dict):
            return []
        q0 = quote[0]
        opens = q0.get("open")
        highs = q0.get("high")
        lows = q0.get("low")
        closes = q0.get("close")
        vols = q0.get("volume")
        if not (isinstance(opens, list) and isinstance(highs, list) and isinstance(lows, list) and isinstance(closes, list)):
            return []
        if vols is not None and not isinstance(vols, list):
            return []

        points: list[MarketHistoryPoint] = []
        n = min(len(timestamps), len(opens), len(highs), len(lows), len(closes), len(vols) if isinstance(vols, list) else len(closes))
        for i in range(n):
            ts = timestamps[i]
            if not isinstance(ts, (int, float)):
                continue
            o = opens[i]
            h = highs[i]
            lo = lows[i]
            c = closes[i]
            v = vols[i] if isinstance(vols, list) else None
            if o is None or h is None or lo is None or c is None:
                continue
            open_ = float(o)
            high = float(h)
            low = float(lo)
            close = float(c)
            vol = float(v) if isinstance(v, (int, float)) else 0.0
            day = datetime.fromtimestamp(float(ts), tz=timezone.utc).date()
            points.append(
                MarketHistoryPoint(
                    day=day,
                    open=open_,
                    high=high,
                    low=low,
                    close=close,
                    volume=vol,
                )
            )

        points.sort(key=lambda p: p.day)
        return points

    async def _fetch_histories_yfinance_batch(
        self,
        tickers: list[str],
        *,
        period: str = "60d",
    ) -> dict[str, list[MarketHistoryPoint]]:
        tickers2 = [str(t).strip().upper() for t in tickers if str(t).strip()]
        tickers2 = list(dict.fromkeys(tickers2))
        if not tickers2:
            return {}

        def _download():
            return yf.download(
                tickers=" ".join(tickers2),
                period=period,
                interval="1d",
                group_by="ticker",
                threads=True,
                auto_adjust=False,
                progress=False,
            )

        df = await asyncio.to_thread(_download)
        if df is None:
            return {}

        try:
            import pandas as pd  # type: ignore
        except Exception:
            return {}

        if not hasattr(df, "index") or len(df.index) == 0:
            return {}

        out: dict[str, list[MarketHistoryPoint]] = {}
        idx = df.index

        def _mk_points(sub) -> list[MarketHistoryPoint]:
            pts: list[MarketHistoryPoint] = []
            for ts, row in sub.iterrows():
                try:
                    dd = ts.date() if hasattr(ts, "date") else date.fromisoformat(str(ts)[:10])
                    o = row.get("Open")
                    h = row.get("High")
                    lo = row.get("Low")
                    c = row.get("Close")
                    v = row.get("Volume")
                    if o is None or h is None or lo is None or c is None:
                        continue
                    open_ = float(o)
                    high = float(h)
                    low = float(lo)
                    close = float(c)
                    vol = float(v) if v is not None else 0.0
                    if any(math.isnan(x) or math.isinf(x) for x in (open_, high, low, close, vol)):
                        continue
                    pts.append(
                        MarketHistoryPoint(
                            day=dd, open=open_, high=high, low=low, close=close, volume=vol
                        )
                    )
                except Exception:
                    continue
            pts.sort(key=lambda p: p.day)
            return pts

        # Single-ticker output is a normal DataFrame.
        if not isinstance(getattr(df, "columns", None), pd.MultiIndex):
            t = tickers2[0]
            out[t] = _mk_points(df)
            return out

        cols = df.columns
        lvl0 = set(cols.get_level_values(0))
        lvl1 = set(cols.get_level_values(1))
        for t in tickers2:
            try:
                if t in lvl0:
                    sub = df[t]
                elif t in lvl1:
                    sub = df.xs(t, axis=1, level=1)
                else:
                    continue
                pts = _mk_points(sub)
                if pts:
                    out[t] = pts
            except Exception:
                continue

        return out

    async def _fetch_history_finnhub(self, ticker: str) -> list[MarketHistoryPoint]:
        if not self._finnhub_api_key:
            return []
        try:
            import finnhub  # type: ignore
        except Exception:
            return []

        sym = _to_yahoo_symbol(ticker)
        if not sym:
            return []

        client = finnhub.Client(api_key=self._finnhub_api_key)
        now = datetime.now(timezone.utc)
        frm = int((now.timestamp()) - 120 * 86400)
        to = int(now.timestamp())

        # finnhub-python is sync; run in thread.
        def _candles():
            return client.stock_candles(sym, "D", frm, to)

        payload = await asyncio.to_thread(_candles)
        if not isinstance(payload, dict) or payload.get("s") != "ok":
            return []

        ts = payload.get("t")
        o = payload.get("o")
        h = payload.get("h")
        lo = payload.get("l")
        c = payload.get("c")
        v = payload.get("v")
        if not all(isinstance(x, list) for x in (ts, o, h, lo, c)):
            return []

        pts: list[MarketHistoryPoint] = []
        n = min(len(ts), len(o), len(h), len(lo), len(c), len(v) if isinstance(v, list) else len(c))
        for i in range(n):
            try:
                dd = datetime.fromtimestamp(float(ts[i]), tz=timezone.utc).date()
                open_ = float(o[i])
                high = float(h[i])
                low = float(lo[i])
                close = float(c[i])
                vol = float(v[i]) if isinstance(v, list) and i < len(v) else 0.0
                if any(math.isnan(x) or math.isinf(x) for x in (open_, high, low, close, vol)):
                    continue
                pts.append(
                    MarketHistoryPoint(
                        day=dd, open=open_, high=high, low=low, close=close, volume=vol
                    )
                )
            except Exception:
                continue
        pts.sort(key=lambda p: p.day)
        return pts

    async def fetch_histories(
        self,
        tickers: list[str],
        *,
        period: str = "60d",
        attempts: int = 2,
        backoff_seconds: float = 1.0,
    ) -> tuple[dict[str, list[MarketHistoryPoint]], str]:
        tickers2 = [str(t).strip().upper() for t in tickers if str(t).strip()]
        tickers2 = list(dict.fromkeys(tickers2))
        if not tickers2:
            return {}, "none"

        last_exc: Exception | None = None
        out: dict[str, list[MarketHistoryPoint]] = {}
        providers_used: set[str] = set()
        for attempt in range(attempts):
            try:
                t0 = time.perf_counter()
                out = await self._fetch_histories_yfinance_batch(tickers2, period=period)
                if out:
                    providers_used.add("yfinance")
                    self._log.info(
                        "yfinance batch ok: %d/%d tickers in %.2fs",
                        len(out),
                        len(tickers2),
                        time.perf_counter() - t0,
                    )
                    break
            except Exception as e:
                last_exc = e
                self._log.warning("yfinance batch failed (attempt %d/%d): %s", attempt + 1, attempts, e)
            if attempt + 1 < attempts:
                await asyncio.sleep(backoff_seconds)

        missing = [t for t in tickers2 if t not in out]

        # Fallback: Finnhub per-ticker (only if configured) for missing tickers.
        if missing and self._finnhub_api_key:
            for t in list(missing):
                for attempt in range(attempts):
                    try:
                        pts = await self._fetch_history_finnhub(t)
                        if pts:
                            out[t] = pts
                            providers_used.add("finnhub")
                        break
                    except Exception:
                        if attempt + 1 < attempts:
                            await asyncio.sleep(backoff_seconds)
                # update missing set
            missing = [t for t in tickers2 if t not in out]

        # Final fallback: Yahoo chart per-ticker (no auth) for missing tickers.
        if missing:
            for t in list(missing):
                for attempt in range(attempts):
                    try:
                        pts = await self._fetch_history_yahoo(t)
                        if pts:
                            out[t] = pts
                            providers_used.add("yahoo_chart")
                        break
                    except Exception:
                        if attempt + 1 < attempts:
                            await asyncio.sleep(backoff_seconds)

        if out:
            src = next(iter(providers_used)) if len(providers_used) == 1 else "mixed"
            return out, src

        if last_exc is not None:
            raise last_exc
        return {}, "none"

    async def fetch_history(self, ticker: str) -> tuple[list[MarketHistoryPoint], str]:
        m, src = await self.fetch_histories([ticker], period="60d")
        t = str(ticker).strip().upper()
        return (m.get(t) or []), src

    async def analyze(self, ticker: str) -> dict[str, Any]:
        history, source = await self.fetch_history(ticker)
        closes = [p.close for p in history]
        highs = [p.high for p in history]
        lows = [p.low for p in history]
        vols = [p.volume for p in history]
        return self._analyze_from_ohlcv(
            ticker, source=source, closes=closes, highs=highs, lows=lows, volumes=vols
        )

    async def fetch_and_analyze(
        self, ticker: str
    ) -> tuple[dict[str, Any], dict[str, list[float]]]:
        history, source = await self.fetch_history(ticker)
        closes = [p.close for p in history]
        highs = [p.high for p in history]
        lows = [p.low for p in history]
        vols = [p.volume for p in history]
        market = self._analyze_from_ohlcv(
            ticker, source=source, closes=closes, highs=highs, lows=lows, volumes=vols
        )
        series = {"closes": closes, "highs": highs, "lows": lows, "volumes": vols}
        return market, series

