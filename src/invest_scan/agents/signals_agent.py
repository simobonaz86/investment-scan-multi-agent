from __future__ import annotations

import math
from typing import Any


def _sma(values: list[float], window: int) -> float | None:
    if window <= 0 or len(values) < window:
        return None
    xs = values[-window:]
    return sum(xs) / len(xs)


def _rsi(values: list[float], period: int = 14) -> float | None:
    if period <= 0 or len(values) < period + 1:
        return None
    gains: list[float] = []
    losses: list[float] = []
    for i in range(-period, 0):
        delta = values[i] - values[i - 1]
        if delta >= 0:
            gains.append(delta)
            losses.append(0.0)
        else:
            gains.append(0.0)
            losses.append(-delta)

    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    rsi = 100.0 - (100.0 / (1.0 + rs))
    if math.isnan(rsi) or math.isinf(rsi):
        return None
    return float(rsi)


def _stdev(values: list[float], window: int) -> float | None:
    if window <= 1 or len(values) < window:
        return None
    xs = values[-window:]
    mean = sum(xs) / len(xs)
    # Bollinger Bands conventionally use population standard deviation over the window.
    var = sum((x - mean) ** 2 for x in xs) / len(xs)
    sd = math.sqrt(var)
    if math.isnan(sd) or math.isinf(sd):
        return None
    return float(sd)


def _bollinger_width_pct(values: list[float], window: int = 20) -> float | None:
    """
    (Upper - Lower) / Middle where:
      Middle = SMA(window)
      Upper/Lower = Middle +/- 2*stdev(window)
    """
    mid = _sma(values, window)
    sd = _stdev(values, window)
    if mid is None or sd is None or mid == 0:
        return None
    # Upper - Lower = 4 * sd
    return float((4.0 * sd) / mid)


def _bollinger_width_percentile(values: list[float], *, window: int = 20, lookback: int = 60) -> float | None:
    if window <= 1 or lookback <= 1 or len(values) < window + 2:
        return None
    widths: list[float] = []
    start = max(window, len(values) - lookback)
    for i in range(start, len(values) + 1):
        w = _bollinger_width_pct(values[:i], window=window)
        if w is None:
            continue
        widths.append(float(w))
    if len(widths) < 5:
        return None
    last = widths[-1]
    # percentile rank in [0,1]
    return float(sum(1 for x in widths if x <= last) / len(widths))


class SignalsAgent:
    def analyze(
        self, closes: list[float], *, market: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        last = closes[-1] if closes else None
        sma20 = _sma(closes, 20)
        sma50 = _sma(closes, 50)
        rsi14 = _rsi(closes, 14)

        middle_band = sma20
        sd20 = _stdev(closes, 20)
        upper_band = (
            (middle_band + (2.0 * sd20)) if (middle_band is not None and sd20 is not None) else None
        )
        lower_band = (
            (middle_band - (2.0 * sd20)) if (middle_band is not None and sd20 is not None) else None
        )
        bollinger_width_pct = _bollinger_width_pct(closes, window=20)
        bollinger_width_percentile_60 = _bollinger_width_percentile(closes, window=20, lookback=60)
        bollinger_position = None
        if last is not None and upper_band is not None and lower_band is not None:
            denom = upper_band - lower_band
            if denom != 0:
                bollinger_position = float((last - lower_band) / denom)

        trend: str | None = None
        if last is not None and sma20 is not None and sma50 is not None:
            if last > sma20 > sma50:
                trend = "bullish"
            elif last < sma20 < sma50:
                trend = "bearish"
            else:
                trend = "mixed"

        momentum_score: float | None = None
        if market:
            r1w = market.get("return_1w")
            r1m = market.get("return_1m")
            r3m = market.get("return_3m")
            vals = [v for v in [r1w, r1m, r3m] if isinstance(v, (int, float))]
            if vals:
                # heuristic: favor shorter horizons slightly
                momentum_score = (
                    0.5 * float(r1w or 0.0)
                    + 0.3 * float(r1m or 0.0)
                    + 0.2 * float(r3m or 0.0)
                )

        mean_reversion: str | None = None
        if sma20 is not None and last is not None:
            oversold = (
                (
                    (rsi14 is not None and rsi14 <= 30.0)
                    or (bollinger_position is not None and bollinger_position < 0.05)
                )
                and last < sma20 * 0.98
            )
            overbought = (
                (
                    (rsi14 is not None and rsi14 >= 70.0)
                    or (bollinger_position is not None and bollinger_position > 0.95)
                )
                and last > sma20 * 1.02
            )
            if oversold:
                mean_reversion = "oversold"
            elif overbought:
                mean_reversion = "overbought"
            else:
                mean_reversion = "neutral"

        return {
            "last": last,
            "sma20": sma20,
            "sma50": sma50,
            "rsi14": rsi14,
            "bollinger_upper": upper_band,
            "bollinger_lower": lower_band,
            "bollinger_position": bollinger_position,
            "bollinger_width_pct": bollinger_width_pct,
            "bollinger_width_percentile_60": bollinger_width_percentile_60,
            "trend": trend,
            "momentum_score": momentum_score,
            "mean_reversion": mean_reversion,
            "volume_spike": (market or {}).get("volume_spike") if market else None,
        }

