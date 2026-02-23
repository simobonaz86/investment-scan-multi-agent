from __future__ import annotations

from typing import Any


class RiskAgent:
    def score(self, *, volatility_60d_ann: float | None) -> dict[str, Any]:
        if volatility_60d_ann is None:
            return {"risk_score": None, "risk_level": "unknown"}

        v = max(0.0, float(volatility_60d_ann))
        # heuristic mapping: 0.10 -> 20, 0.25 -> 50, 0.45 -> 80
        score = int(max(0.0, min(100.0, (v / 0.45) * 80.0)))

        if score < 30:
            level = "low"
        elif score < 60:
            level = "medium"
        else:
            level = "high"

        return {"risk_score": score, "risk_level": level, "volatility_60d_ann": v}

    def plan_trade(
        self,
        *,
        cash_usd: float | None,
        entry_price: float | None,
        atr14: float | None,
        risk_per_trade_pct: float = 0.01,
        stop_atr_multiple: float = 2.0,
        min_position_usd: float = 100.0,
    ) -> dict[str, Any]:
        if cash_usd is None or entry_price is None or atr14 is None:
            return {"enabled": False, "reason": "missing_cash_or_market_data"}

        cash = float(max(0.0, cash_usd))
        entry = float(entry_price)
        atr = float(atr14)
        if cash <= 0 or entry <= 0 or atr <= 0:
            return {"enabled": False, "reason": "invalid_inputs"}

        risk_budget = cash * float(max(0.0, min(0.05, risk_per_trade_pct)))
        stop_distance = atr * float(max(0.5, min(10.0, stop_atr_multiple)))
        stop_loss = entry - stop_distance
        if stop_loss <= 0:
            return {"enabled": False, "reason": "stop_loss_nonpositive"}

        risk_per_share = entry - stop_loss
        if risk_per_share <= 0:
            return {"enabled": False, "reason": "risk_per_share_nonpositive"}

        shares_by_risk = int(risk_budget // risk_per_share)
        shares_by_cash = int(cash // entry)
        shares = int(max(0, min(shares_by_risk, shares_by_cash)))
        notional = shares * entry
        valid_cash = notional <= cash and shares > 0 and notional >= min_position_usd

        return {
            "enabled": True,
            "cash_usd": cash,
            "entry_price": entry,
            "stop_loss": stop_loss,
            "risk_budget_usd": risk_budget,
            "risk_per_share": risk_per_share,
            "shares": shares,
            "notional_usd": notional,
            "cash_valid": bool(valid_cash),
        }

