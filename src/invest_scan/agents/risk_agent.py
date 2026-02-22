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

