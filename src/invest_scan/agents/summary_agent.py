from __future__ import annotations

from typing import Any


class SummaryAgent:
    def summarize(self, ticker_report: dict[str, Any]) -> str:
        t = ticker_report.get("ticker")
        md = ticker_report.get("market", {})
        sig = ticker_report.get("signals", {})
        risk = ticker_report.get("risk", {})

        last = md.get("last_close")
        day_ret = md.get("day_return")
        trend = sig.get("trend")
        rsi = sig.get("rsi14")
        risk_level = risk.get("risk_level")

        parts: list[str] = []
        parts.append(f"{t}: last={last}")
        if day_ret is not None:
            parts.append(f"day_return={day_ret:.2%}")
        if trend:
            parts.append(f"trend={trend}")
        if rsi is not None:
            parts.append(f"rsi14={rsi:.1f}")
        if risk_level:
            parts.append(f"risk={risk_level}")
        return " | ".join(parts)

