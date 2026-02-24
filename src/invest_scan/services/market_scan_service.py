from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

import httpx

from invest_scan import db
from invest_scan.agents import MarketDataAgent, RiskAgent, SignalsAgent
from invest_scan.services.portfolio_service import PortfolioService
from invest_scan.services.recommendation_service import RecommendationService
from invest_scan.services.universe_service import UniverseService
from invest_scan.settings import Settings


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _rating(score: float, mechanisms: list[str]) -> str:
    mech_count = len(set(mechanisms))
    s = float(score)
    if s >= 20.0 and mech_count >= 2:
        return "Very Strong"
    if s >= 14.0:
        return "Strong"
    if s >= 8.0:
        return "Light"
    return "Not strong"


def _score_and_reasons(*, market: dict[str, Any], signals: dict[str, Any]) -> dict[str, Any]:
    score = 0.0
    reasons: list[str] = []
    mechanisms: list[str] = []

    trend = signals.get("trend")
    sma20 = signals.get("sma20")
    sma50 = signals.get("sma50")
    last = signals.get("last")
    if isinstance(sma20, (int, float)) and isinstance(sma50, (int, float)) and float(sma20) > float(sma50):
        score += 4
        reasons.append("uptrend (SMA20 above SMA50)")
        mechanisms.append("trend_following")

    if trend == "bullish":
        score += 6
        reasons.append("strong uptrend (price > SMA20 > SMA50)")
        mechanisms.append("trend_following_strong")
    elif trend == "bearish":
        score -= 10
        reasons.append("bearish trend (price < SMA20 < SMA50)")

    mom = signals.get("momentum_score")
    if isinstance(mom, (int, float)):
        score += float(mom) * 100.0
        if mom > 0:
            reasons.append(f"positive momentum ({mom:.2%})")
            mechanisms.append("momentum")
        else:
            reasons.append(f"negative momentum ({mom:.2%})")

    # Trend pullback: price below SMA20 but above SMA50 in an uptrend.
    if (
        isinstance(last, (int, float))
        and isinstance(sma20, (int, float))
        and isinstance(sma50, (int, float))
        and float(sma20) > float(sma50)
        and float(last) < float(sma20)
        and float(last) > float(sma50)
    ):
        score += 6
        reasons.append("pullback in uptrend (price below SMA20 but above SMA50)")
        mechanisms.append("pullback_in_uptrend")

    # Volatility squeeze (Bollinger width unusually low).
    bw = signals.get("bollinger_width_pct")
    bw_pct = signals.get("bollinger_width_percentile_60")
    squeeze = False
    if isinstance(bw, (int, float)) and float(bw) <= 0.06:
        squeeze = True
    if isinstance(bw_pct, (int, float)) and float(bw_pct) <= 0.20:
        squeeze = True
    if squeeze:
        score += 4
        reasons.append("volatility squeeze (tight Bollinger Bands)")
        mechanisms.append("volatility_squeeze")
        if trend == "bullish" or (
            isinstance(sma20, (int, float)) and isinstance(sma50, (int, float)) and float(sma20) > float(sma50)
        ):
            score += 2
            reasons.append("squeeze aligned with uptrend")

    mr = signals.get("mean_reversion")
    if mr == "oversold":
        score += 4
        reasons.append("mean reversion: oversold")
        mechanisms.append("mean_reversion")
    elif mr == "overbought":
        score -= 6
        reasons.append("mean reversion: overbought")
        mechanisms.append("mean_reversion_overbought")

    bp = signals.get("bollinger_position")
    if isinstance(bp, (int, float)):
        if float(bp) < 0.05:
            score += 3
            reasons.append("price below lower Bollinger Band")
            mechanisms.append("bollinger_extreme_low")
        elif float(bp) > 0.95:
            score -= 3
            reasons.append("price above upper Bollinger Band")
            mechanisms.append("bollinger_extreme_high")

    if market.get("volume_spike") is True:
        score += 2
        r = market.get("volume_spike_ratio")
        if isinstance(r, (int, float)):
            reasons.append(f"volume spike ({float(r):.1f}x 20d avg)")
        else:
            reasons.append("volume spike")
        mechanisms.append("volume_spike")

    vol = market.get("volatility_60d_ann")
    if isinstance(vol, (int, float)) and float(vol) > 0.6:
        score -= 3
        reasons.append("very high volatility")

    mechs = list(dict.fromkeys(mechanisms))
    return {
        "score": float(score),
        "reasons": reasons,
        "mechanisms": mechs,
        "rating": _rating(score, mechs),
    }


class MarketScanService:
    def __init__(
        self,
        *,
        settings: Settings,
        http: httpx.AsyncClient,
        universe: UniverseService,
        portfolio: PortfolioService,
        recommendations: RecommendationService | None = None,
    ) -> None:
        self._settings = settings
        self._http = http
        self._universe = universe
        self._portfolio = portfolio
        self._recs = recommendations

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
                scored = _score_and_reasons(market=market2, signals=signals)
                score = float(scored["score"])
                reasons = list(scored["reasons"])
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
                    "rating": scored.get("rating"),
                    "mechanisms": scored.get("mechanisms") or [],
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
        ranked = items2[:top_n]
        candidates = [x for x in ranked if float(x.get("score") or 0.0) >= min_score]

        if self._recs is not None:
            for c in ranked:
                try:
                    await self._recs.upsert_from_candidate(
                        source_scan_id=str(scan_id),
                        candidate=c,
                        cash_usd=cash_usd,
                    )
                except Exception:
                    continue

        return {
            "scan_id": str(scan_id),
            "generated_at": _utcnow_iso(),
            "universe_source": uni.get("source"),
            "universe_size": len(tickers),
            "scored_size": len(items2),
            "top_n": top_n,
            "min_score": min_score,
            "ranked": ranked,
            "candidates": candidates,
        }

    async def run_and_persist(self, *, scan_id: UUID) -> None:
        await db.mark_market_running(self._settings.db_path, scan_id)
        try:
            result = await self.run(scan_id=scan_id)
            await db.set_market_result(self._settings.db_path, scan_id, result)
        except Exception as e:
            await db.set_market_failed(self._settings.db_path, scan_id, f"{e.__class__.__name__}: {e}")

