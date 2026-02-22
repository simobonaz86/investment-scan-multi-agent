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


class SignalsAgent:
    def analyze(self, closes: list[float]) -> dict[str, Any]:
        last = closes[-1] if closes else None
        sma20 = _sma(closes, 20)
        sma50 = _sma(closes, 50)
        rsi14 = _rsi(closes, 14)

        trend: str | None = None
        if last is not None and sma20 is not None and sma50 is not None:
            if last > sma20 > sma50:
                trend = "bullish"
            elif last < sma20 < sma50:
                trend = "bearish"
            else:
                trend = "mixed"

        return {
            "last": last,
            "sma20": sma20,
            "sma50": sma50,
            "rsi14": rsi14,
            "trend": trend,
        }

