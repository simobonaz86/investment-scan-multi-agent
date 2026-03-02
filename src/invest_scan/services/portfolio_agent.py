from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from invest_scan.settings import Settings


@dataclass(frozen=True)
class PlanLine:
    ticker: str
    rec_id: str | None
    trigger_status: str | None
    score: float
    rating: str | None
    entry: float
    stop: float
    take: float | None
    shares: int
    notional_usd: float
    risk_usd: float
    rr: float | None
    notes: str | None = None


class ShortHorizonPortfolioAgent:
    """
    Builds a small trade plan for a tactical sleeve (default 1% of total portfolio).
    This is not execution; it's sizing + selection.
    """

    def __init__(self, *, settings: Settings) -> None:
        self._s = settings

    def build_plan(
        self,
        *,
        recommendations: list[dict[str, Any]],
        intraday_watchlist: list[dict[str, Any]] | None,
    ) -> dict[str, Any]:
        total = float(self._s.total_portfolio_usd or 0.0)
        sleeve_pct = float(self._s.tactical_sleeve_pct or 0.01)
        sleeve_value = total * sleeve_pct
        if total <= 0 or sleeve_value <= 0:
            return {
                "ok": False,
                "error": "missing_total_portfolio_usd",
                "total_portfolio_usd": total,
                "sleeve_pct": sleeve_pct,
                "sleeve_value_usd": sleeve_value,
                "lines": [],
            }

        max_positions = int(max(1, self._s.tactical_max_positions or 4))
        risk_pct = float(self._s.tactical_risk_per_trade_pct or 0.01)
        risk_per_trade = sleeve_value * risk_pct
        max_pos_pct = float(self._s.tactical_max_position_pct or 0.35)
        max_notional_per_pos = sleeve_value * max_pos_pct

        trig_by_ticker: dict[str, dict[str, Any]] = {}
        for it in intraday_watchlist or []:
            t = str(it.get("ticker") or "").strip().upper()
            if t:
                trig_by_ticker[t] = it

        # Prefer TRIGGERED, then WAITING; ignore TOO_LATE/INVALIDATED/DATA_ERROR.
        def trig_rank(t: str) -> int:
            st = str((trig_by_ticker.get(t) or {}).get("status") or "WAITING").upper()
            if st == "TRIGGERED":
                return 0
            if st == "WAITING":
                return 1
            return 9

        recs = list(recommendations or [])
        recs.sort(
            key=lambda r: (
                trig_rank(str(r.get("ticker") or "").strip().upper()),
                -float(r.get("score") or 0.0),
            )
        )

        used = 0.0
        lines: list[PlanLine] = []
        for r in recs:
            if len(lines) >= max_positions:
                break
            t = str(r.get("ticker") or "").strip().upper()
            if not t:
                continue
            trig = trig_by_ticker.get(t) or {}
            st = str(trig.get("status") or "WAITING").upper()
            if st in {"TOO_LATE", "INVALIDATED", "DATA_ERROR"}:
                continue

            entry = float(r.get("entry_price") or 0.0)
            stop = float(r.get("stop_loss") or 0.0)
            take = float(r.get("take_profit")) if r.get("take_profit") is not None else None
            if entry <= 0 or stop <= 0 or stop >= entry:
                continue
            stop_dist = entry - stop
            risk_per_share = stop_dist
            if risk_per_share <= 0:
                continue

            shares_by_risk = int(math.floor(risk_per_trade / risk_per_share))
            shares_by_max_notional = int(math.floor(max_notional_per_pos / entry))
            shares = int(max(0, min(shares_by_risk, shares_by_max_notional)))
            if shares <= 0:
                continue

            notional = shares * entry
            if used + notional > sleeve_value:
                # Try to scale down to remaining cash in sleeve.
                remaining = max(0.0, sleeve_value - used)
                shares = int(math.floor(remaining / entry))
                if shares <= 0:
                    continue
                notional = shares * entry

            rr = None
            if take is not None and stop_dist > 0:
                rr = (take - entry) / stop_dist

            used += notional
            lines.append(
                PlanLine(
                    ticker=t,
                    rec_id=str(r.get("rec_id")) if r.get("rec_id") is not None else None,
                    trigger_status=str(st),
                    score=float(r.get("score") or 0.0),
                    rating=r.get("rating"),
                    entry=entry,
                    stop=stop,
                    take=take,
                    shares=shares,
                    notional_usd=float(notional),
                    risk_usd=float(shares * stop_dist),
                    rr=float(rr) if rr is not None else None,
                    notes="triggered_now" if st == "TRIGGERED" else None,
                )
            )

        return {
            "ok": True,
            "total_portfolio_usd": total,
            "sleeve_pct": sleeve_pct,
            "sleeve_value_usd": sleeve_value,
            "risk_per_trade_usd": risk_per_trade,
            "max_positions": max_positions,
            "max_notional_per_position_usd": max_notional_per_pos,
            "allocated_usd": used,
            "lines": [line.__dict__ for line in lines],
        }

