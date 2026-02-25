from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal


@dataclass(frozen=True)
class IntradayCandle:
    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


TriggerStatus = Literal["WAITING", "TRIGGERED", "TOO_LATE", "INVALIDATED", "DATA_ERROR"]


def _ema(values: list[float], period: int) -> float | None:
    if not values or period <= 1:
        return None
    k = 2.0 / (period + 1.0)
    ema = float(values[0])
    for v in values[1:]:
        ema = float(v) * k + ema * (1.0 - k)
    return ema


def _vwap(candles: list[IntradayCandle]) -> float | None:
    # VWAP approximation over provided candles.
    num = 0.0
    den = 0.0
    for c in candles:
        px = (c.high + c.low + c.close) / 3.0
        vol = float(c.volume or 0.0)
        if vol <= 0:
            continue
        num += px * vol
        den += vol
    if den <= 0:
        return None
    return num / den


def _setup_type(mechanisms: list[str]) -> str:
    ms = {str(x) for x in (mechanisms or [])}
    if "volatility_squeeze" in ms:
        return "squeeze_breakout"
    if "pullback_in_uptrend" in ms:
        return "pullback_reclaim"
    if "mean_reversion" in ms or "bollinger_extreme_low" in ms:
        return "mean_reversion_bounce"
    return "pullback_reclaim"


def _too_late_threshold(rating: str | None) -> float:
    r = (rating or "").strip().lower()
    if "very" in r:
        return 0.025
    if "strong" in r:
        return 0.020
    if "light" in r or "medium" in r:
        return 0.018
    return 0.015


class IntradayTriggerAgent:
    def evaluate(
        self,
        *,
        rec: dict[str, Any],
        candles: list[IntradayCandle] | None,
        interval: str,
    ) -> dict[str, Any]:
        ticker = str(rec.get("ticker") or "").strip().upper()
        mechanisms = rec.get("mechanisms") or []
        rating = rec.get("rating")
        stop_loss = rec.get("stop_loss")
        stop = float(stop_loss) if isinstance(stop_loss, (int, float)) else None

        if not candles or len(candles) < 25:
            return {
                "ticker": ticker,
                "rec_id": rec.get("rec_id"),
                "score": rec.get("score"),
                "rating": rating,
                "setup_type": _setup_type(mechanisms),
                "status": "DATA_ERROR",
                "interval": interval,
                "reason": "insufficient_intraday_history",
                "details": {},
            }

        cs = candles[-80:]  # last ~20h (15m bars) max
        last = cs[-1]
        prev = cs[-2]

        # Invalidation first.
        if stop is not None and isinstance(stop, (int, float)):
            if float(last.low) < float(stop):
                return {
                    "ticker": ticker,
                    "rec_id": rec.get("rec_id"),
                    "score": rec.get("score"),
                    "rating": rating,
                    "setup_type": _setup_type(mechanisms),
                    "status": "INVALIDATED",
                    "interval": interval,
                    "trigger_price": None,
                    "triggered_at": None,
                    "last_price": float(last.close),
                    "extension_pct": None,
                    "reason": "below_stop_loss",
                    "details": {"stop_loss": float(stop)},
                }

        closes = [float(x.close) for x in cs]
        highs = [float(x.high) for x in cs]
        vols = [float(x.volume or 0.0) for x in cs]
        ema20 = _ema(closes, 20)
        vwap = _vwap(cs[-40:])  # ~10h window for stability
        range_high_20 = max(highs[-20:]) if len(highs) >= 20 else max(highs)
        avg_vol_20 = (sum(vols[-20:]) / len(vols[-20:])) if len(vols) >= 20 else None
        vol_ratio = (vols[-1] / avg_vol_20) if (avg_vol_20 and avg_vol_20 > 0) else None

        setup = _setup_type(mechanisms)
        buffer_pct = 0.0015
        triggered = False
        trigger_price: float | None = None
        reason = ""

        if setup == "squeeze_breakout":
            trigger_price = float(range_high_20)
            cond_break = float(last.close) > float(range_high_20) * (1.0 + buffer_pct)
            cond_vwap = vwap is None or float(last.close) > float(vwap)
            cond_vol = vol_ratio is None or float(vol_ratio) >= 1.3
            triggered = bool(cond_break and cond_vwap and cond_vol)
            reason = "breakout_above_range_high" if triggered else "waiting_for_breakout"
        elif setup == "mean_reversion_bounce":
            # Stabilization + reclaim (EMA/VWAP).
            reclaim = False
            level = None
            if ema20 is not None and float(last.close) > float(ema20):
                reclaim = True
                level = float(ema20)
            if vwap is not None and float(last.close) > float(vwap):
                reclaim = True
                level = float(vwap) if level is None else max(level, float(vwap))
            stabilizing = float(last.close) > float(prev.close)
            triggered = bool(reclaim and stabilizing)
            trigger_price = float(level) if level is not None else None
            reason = "reclaim_mean" if triggered else "waiting_for_stabilization"
        else:
            # pullback_reclaim default
            reclaim = False
            level = None
            if ema20 is not None and float(last.close) > float(ema20):
                reclaim = True
                level = float(ema20)
            if vwap is not None and float(last.close) > float(vwap):
                reclaim = True
                level = float(vwap) if level is None else max(level, float(vwap))
            triggered = bool(reclaim)
            trigger_price = float(level) if level is not None else None
            reason = "reclaim_vwap_ema" if triggered else "waiting_for_reclaim"

        # Too-late logic (avoid buying after a fast extension).
        ext_pct = None
        if triggered and trigger_price and trigger_price > 0:
            ext_pct = (float(last.close) - float(trigger_price)) / float(trigger_price)
            if ext_pct > _too_late_threshold(rating):
                return {
                    "ticker": ticker,
                    "rec_id": rec.get("rec_id"),
                    "score": rec.get("score"),
                    "rating": rating,
                    "setup_type": setup,
                    "status": "TOO_LATE",
                    "interval": interval,
                    "trigger_price": float(trigger_price),
                    "triggered_at": None,
                    "last_price": float(last.close),
                    "extension_pct": float(ext_pct),
                    "reason": "too_extended_after_trigger",
                    "details": {
                        "ema20": ema20,
                        "vwap": vwap,
                        "range_high_20": float(range_high_20),
                        "vol_ratio": vol_ratio,
                    },
                }

        status: TriggerStatus = "TRIGGERED" if triggered else "WAITING"
        details = {
            "ema20": ema20,
            "vwap": vwap,
            "range_high_20": float(range_high_20),
            "vol_ratio": vol_ratio,
            "last_ts": last.ts.isoformat(),
        }
        # Clean NaNs
        for k, v in list(details.items()):
            if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
                details[k] = None

        return {
            "ticker": ticker,
            "rec_id": rec.get("rec_id"),
            "score": rec.get("score"),
            "rating": rating,
            "setup_type": setup,
            "status": status,
            "interval": interval,
            "trigger_price": float(trigger_price) if trigger_price is not None else None,
            "triggered_at": None,
            "last_price": float(last.close),
            "extension_pct": float(ext_pct) if ext_pct is not None else None,
            "reason": reason,
            "details": details,
        }

