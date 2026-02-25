from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

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

        self._log = logging.getLogger(__name__)
        self._market = MarketDataAgent(http, finnhub_api_key=settings.finnhub_api_key)
        self._signals = SignalsAgent()
        self._risk = RiskAgent()

    async def run(self, *, scan_id: UUID) -> dict[str, Any]:
        t0 = time.perf_counter()
        uni = await self._universe.get_universe()
        tickers = list(uni.get("tickers") or [])
        tickers = tickers[: int(self._settings.sp500_ranking_max_tickers)]

        cash_usd = (await self._portfolio.get_portfolio()).cash_usd
        self._log.info("Market scan start: %d tickers", len(tickers))

        # One batch market-data call for the entire universe.
        fetch_t0 = time.perf_counter()
        histories, source = await self._market.fetch_histories(
            tickers, period="90d", attempts=2, backoff_seconds=1.0
        )
        self._log.info(
            "Market data fetched: %d/%d tickers via %s in %.2fs",
            len(histories),
            len(tickers),
            source,
            time.perf_counter() - fetch_t0,
        )

        scored_items: list[dict[str, Any]] = []
        failures: list[dict[str, Any]] = []
        for t in tickers:
            try:
                pts = histories.get(str(t).strip().upper()) or []
                if len(pts) < 2:
                    failures.append({"ticker": t, "error": "no_history"})
                    continue
                closes = [p.close for p in pts]
                highs = [p.high for p in pts]
                lows = [p.low for p in pts]
                vols = [p.volume for p in pts]
                market2 = self._market._analyze_from_ohlcv(  # noqa: SLF001 (internal helper is OK here)
                    t, source=source, closes=closes, highs=highs, lows=lows, volumes=vols
                )
                if market2.get("error"):
                    failures.append({"ticker": t, "error": str(market2.get("error"))})
                    continue
                signals = self._signals.analyze(closes, market=market2)
                scored = _score_and_reasons(market=market2, signals=signals)
                trade_plan = self._risk.plan_trade(
                    cash_usd=cash_usd,
                    entry_price=market2.get("last_close"),
                    atr14=market2.get("atr14"),
                    risk_per_trade_pct=self._settings.risk_per_trade_pct,
                    stop_atr_multiple=self._settings.stop_atr_multiple,
                    min_position_usd=self._settings.min_position_usd,
                )
                scored_items.append(
                    {
                        "ticker": t,
                        "score": float(scored["score"]),
                        "reasons": list(scored["reasons"]),
                        "rating": scored.get("rating"),
                        "mechanisms": scored.get("mechanisms") or [],
                        "market": market2,
                        "signals": signals,
                        "trade_plan": trade_plan,
                    }
                )
            except Exception as e:
                failures.append({"ticker": t, "error": f"unexpected_error:{e.__class__.__name__}"})

        scored_items.sort(key=lambda x: float(x.get("score") or 0.0), reverse=True)

        top_n = int(max(1, self._settings.marketscan_top_n))
        min_score = float(self._settings.marketscan_min_score)
        ranked = scored_items[:top_n]
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
            "scored_size": len(scored_items),
            "failed_size": len(failures),
            "errors_sample": failures[:8],
            "top_n": top_n,
            "min_score": min_score,
            "ranked": ranked,
            "candidates": candidates,
        }

    async def run_and_persist(self, *, scan_id: UUID) -> None:
        await db.mark_market_running(self._settings.db_path, scan_id)
        t0 = time.perf_counter()
        try:
            result = await self.run(scan_id=scan_id)
            await db.set_market_result(self._settings.db_path, scan_id, result)
            self._log.info(
                "Market scan completed: %d tickers in %.1fs, %d candidates, %d failed",
                int(result.get("universe_size") or 0),
                time.perf_counter() - t0,
                int(len(result.get("candidates") or [])),
                int(result.get("failed_size") or 0),
            )
        except Exception as e:
            await db.set_market_failed(self._settings.db_path, scan_id, f"{e.__class__.__name__}: {e}")

