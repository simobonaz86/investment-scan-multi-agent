from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

import httpx

from invest_scan import db
from invest_scan.agents import MarketDataAgent, RiskAgent, SignalsAgent
from invest_scan.services.portfolio_service import PortfolioService
from invest_scan.services.universe_service import UniverseService
from invest_scan.settings import Settings


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _score_and_reasons(*, market: dict[str, Any], signals: dict[str, Any]) -> tuple[float, list[str]]:
    score = 0.0
    reasons: list[str] = []

    trend = signals.get("trend")
    if trend == "bullish":
        score += 10
        reasons.append("bullish trend (price > SMA20 > SMA50)")
    elif trend == "bearish":
        score -= 10
        reasons.append("bearish trend (price < SMA20 < SMA50)")

    mom = signals.get("momentum_score")
    if isinstance(mom, (int, float)):
        score += float(mom) * 100.0
        if mom > 0:
            reasons.append(f"positive momentum ({mom:.2%})")
        else:
            reasons.append(f"negative momentum ({mom:.2%})")

    mr = signals.get("mean_reversion")
    if mr == "oversold":
        score += 6
        reasons.append("mean reversion: oversold")
    elif mr == "overbought":
        score -= 6
        reasons.append("mean reversion: overbought")

    bp = signals.get("bollinger_position")
    if isinstance(bp, (int, float)):
        if float(bp) < 0.05:
            score += 3
            reasons.append("price below lower Bollinger Band")
        elif float(bp) > 0.95:
            score -= 3
            reasons.append("price above upper Bollinger Band")

    if market.get("volume_spike") is True:
        score += 4
        r = market.get("volume_spike_ratio")
        if isinstance(r, (int, float)):
            reasons.append(f"volume spike ({float(r):.1f}x 20d avg)")
        else:
            reasons.append("volume spike")

    vol = market.get("volatility_60d_ann")
    if isinstance(vol, (int, float)) and float(vol) > 0.6:
        score -= 3
        reasons.append("very high volatility")

    return score, reasons


class MarketScanService:
    def __init__(
        self,
        *,
        settings: Settings,
        http: httpx.AsyncClient,
        universe: UniverseService,
        portfolio: PortfolioService,
    ) -> None:
        self._settings = settings
        self._http = http
        self._universe = universe
        self._portfolio = portfolio

        self._sem = asyncio.Semaphore(settings.max_concurrent_fetches)
        self._market = MarketDataAgent(http)
        self._signals = SignalsAgent()
        self._risk = RiskAgent()

    async def _limited(self, coro):
        async with self._sem:
            return await coro

    async def run(self, *, scan_id: UUID) -> dict[str, Any]:
        uni = await self._universe.get_universe()
        tickers = list(uni.get("tickers") or [])
        tickers = tickers[: int(self._settings.sp500_ranking_max_tickers)]

        cash_usd = (await self._portfolio.get_portfolio()).cash_usd

        async def one(t: str) -> dict[str, Any] | None:
            try:
                market = await self._limited(self._market.analyze(t))
                if market.get("error"):
                    return None
                closes = []  # SignalsAgent only needs closes; market already computed from OHLCV
                # We don't have closes series here (analyze() returns summary), so keep simple:
                # reconstruct minimal closes-based signals from available fields is not possible.
                # Instead, re-fetch series once for a smaller subset in future iterations.
                # For MVP, use trend/rsi computed from closes by calling fetch_and_analyze.
                market2, series = await self._limited(self._market.fetch_and_analyze(t))
                closes = series.get("closes") or []
                signals = self._signals.analyze(closes, market=market2)
                score, reasons = _score_and_reasons(market=market2, signals=signals)
                trade_plan = self._risk.plan_trade(
                    cash_usd=cash_usd,
                    entry_price=market2.get("last_close"),
                    atr14=market2.get("atr14"),
                    risk_per_trade_pct=self._settings.risk_per_trade_pct,
                    stop_atr_multiple=self._settings.stop_atr_multiple,
                    min_position_usd=self._settings.min_position_usd,
                )
                return {
                    "ticker": t,
                    "score": score,
                    "reasons": reasons,
                    "market": market2,
                    "signals": signals,
                    "trade_plan": trade_plan,
                }
            except httpx.HTTPError:
                return None
            except Exception:
                return None

        items = await asyncio.gather(*(one(t) for t in tickers))
        items2 = [x for x in items if x is not None]
        items2.sort(key=lambda x: float(x.get("score") or 0.0), reverse=True)

        top_n = int(max(1, self._settings.marketscan_top_n))
        min_score = float(self._settings.marketscan_min_score)
        candidates = [x for x in items2 if float(x.get("score") or 0.0) >= min_score][:top_n]

        return {
            "scan_id": str(scan_id),
            "generated_at": _utcnow_iso(),
            "universe_source": uni.get("source"),
            "universe_size": len(tickers),
            "scored_size": len(items2),
            "candidates": candidates,
        }

    async def run_and_persist(self, *, scan_id: UUID) -> None:
        await db.mark_market_running(self._settings.db_path, scan_id)
        try:
            result = await self.run(scan_id=scan_id)
            await db.set_market_result(self._settings.db_path, scan_id, result)
        except Exception as e:
            await db.set_market_failed(self._settings.db_path, scan_id, f"{e.__class__.__name__}: {e}")

