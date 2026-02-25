from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

import aiosqlite
import httpx

from invest_scan.agents import IntradayCandle, IntradayTriggerAgent, MarketDataAgent
from invest_scan.settings import Settings
from invest_scan.services.recommendation_service import RecommendationService


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class IntradayService:
    def __init__(
        self,
        *,
        settings: Settings,
        http: httpx.AsyncClient,
        recommendations: RecommendationService,
    ) -> None:
        self._settings = settings
        self._log = logging.getLogger(__name__)
        self._recs = recommendations
        self._market = MarketDataAgent(http, finnhub_api_key=settings.finnhub_api_key)
        self._trigger = IntradayTriggerAgent()

    async def refresh_watchlist(self) -> list[dict[str, Any]]:
        # Take the highest-score active recommendations (latest per ticker already).
        recs = await self._recs.list(status="active", limit=250)
        recs.sort(key=lambda r: float(r.get("score") or 0.0), reverse=True)
        recs = recs[: int(max(1, self._settings.intraday_watchlist_size))]
        tickers = [str(r.get("ticker") or "").strip().upper() for r in recs if str(r.get("ticker") or "").strip()]
        tickers = list(dict.fromkeys(tickers))

        interval = str(self._settings.intraday_interval or "15m").strip()
        period = str(self._settings.intraday_period or "5d").strip()
        raw = await self._market.fetch_intraday_histories(tickers, interval=interval, period=period, chunk_size=20)

        now = _utcnow_iso()
        items: list[dict[str, Any]] = []
        for r in recs:
            t = str(r.get("ticker") or "").strip().upper()
            candles_raw = raw.get(t) or []
            candles: list[IntradayCandle] = []
            for c in candles_raw:
                try:
                    ts = c.get("ts")
                    candles.append(
                        IntradayCandle(
                            ts=ts if isinstance(ts, datetime) else datetime.fromisoformat(str(ts)),
                            open=float(c.get("open") or 0.0),
                            high=float(c.get("high") or 0.0),
                            low=float(c.get("low") or 0.0),
                            close=float(c.get("close") or 0.0),
                            volume=float(c.get("volume") or 0.0),
                        )
                    )
                except Exception:
                    continue

            trig = self._trigger.evaluate(rec=r, candles=candles, interval=interval)
            items.append(
                {
                    "ticker": t,
                    "rec_id": trig.get("rec_id"),
                    "score": float(trig.get("score") or 0.0),
                    "rating": trig.get("rating"),
                    "setup_type": trig.get("setup_type"),
                    "status": trig.get("status"),
                    "trigger_price": trig.get("trigger_price"),
                    "triggered_at": trig.get("triggered_at"),
                    "last_price": trig.get("last_price"),
                    "extension_pct": trig.get("extension_pct"),
                    "interval": trig.get("interval"),
                    "reason": trig.get("reason"),
                    "details": trig.get("details") or {},
                    "updated_at": now,
                }
            )

        await self._upsert(items)
        return await self.get_watchlist(limit=self._settings.intraday_watchlist_size)

    async def _upsert(self, items: list[dict[str, Any]]) -> None:
        if not items:
            return
        async with aiosqlite.connect(self._settings.db_path) as db:
            for it in items:
                await db.execute(
                    """
                    INSERT INTO intraday_watchlist(
                      ticker, rec_id, score, rating, setup_type, status,
                      trigger_price, triggered_at, last_price, extension_pct,
                      interval, reason, details_json, updated_at
                    )
                    VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(ticker) DO UPDATE SET
                      rec_id=excluded.rec_id,
                      score=excluded.score,
                      rating=excluded.rating,
                      setup_type=excluded.setup_type,
                      status=excluded.status,
                      trigger_price=excluded.trigger_price,
                      triggered_at=CASE
                        WHEN intraday_watchlist.triggered_at IS NOT NULL THEN intraday_watchlist.triggered_at
                        WHEN excluded.status='TRIGGERED' THEN excluded.updated_at
                        ELSE NULL
                      END,
                      last_price=excluded.last_price,
                      extension_pct=excluded.extension_pct,
                      interval=excluded.interval,
                      reason=excluded.reason,
                      details_json=excluded.details_json,
                      updated_at=excluded.updated_at
                    """,
                    (
                        it.get("ticker"),
                        it.get("rec_id"),
                        it.get("score"),
                        it.get("rating"),
                        it.get("setup_type"),
                        it.get("status"),
                        it.get("trigger_price"),
                        it.get("triggered_at"),
                        it.get("last_price"),
                        it.get("extension_pct"),
                        it.get("interval"),
                        it.get("reason"),
                        json.dumps(it.get("details") or {}),
                        it.get("updated_at"),
                    ),
                )
            await db.commit()

    async def get_watchlist(self, *, limit: int = 20) -> list[dict[str, Any]]:
        lim = int(max(1, min(200, limit)))
        async with aiosqlite.connect(self._settings.db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                """
                SELECT *
                FROM intraday_watchlist
                ORDER BY COALESCE(score, 0.0) DESC, updated_at DESC
                LIMIT ?
                """,
                (lim,),
            )
            rows = [dict(r) for r in await cur.fetchall()]
        out: list[dict[str, Any]] = []
        for r in rows:
            try:
                r["details"] = json.loads(r.get("details_json") or "{}")
            except Exception:
                r["details"] = {}
            r.pop("details_json", None)
            out.append(r)
        return out

