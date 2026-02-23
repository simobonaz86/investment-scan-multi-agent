from __future__ import annotations

import json
import math
from datetime import datetime, timedelta, timezone
from typing import Any, Literal
from uuid import uuid4

import aiosqlite

from invest_scan.settings import Settings
from invest_scan.services.trade_service import TradeService


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _utcnow_iso() -> str:
    return _utcnow().isoformat()


def _parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    return datetime.fromisoformat(s)


def _row_to_rec(row: dict[str, Any]) -> dict[str, Any]:
    cash_after = float(row["cash_after"]) if row.get("cash_after") is not None else None
    return {
        "rec_id": row["rec_id"],
        "ticker": row["ticker"],
        "strategy": row.get("strategy"),
        "score": float(row["score"]) if row.get("score") is not None else None,
        "reasons": json.loads(row["reasons"]) if row.get("reasons") else [],
        "entry_price": float(row["entry_price"]) if row.get("entry_price") is not None else None,
        "stop_loss": float(row["stop_loss"]) if row.get("stop_loss") is not None else None,
        "take_profit": float(row["take_profit"]) if row.get("take_profit") is not None else None,
        "shares": int(row["shares"]) if row.get("shares") is not None else None,
        "notional_usd": float(row["notional_usd"]) if row.get("notional_usd") is not None else None,
        "max_loss_usd": float(row["max_loss_usd"]) if row.get("max_loss_usd") is not None else None,
        "risk_reward_ratio": float(row["risk_reward_ratio"]) if row.get("risk_reward_ratio") is not None else None,
        "cash_after": cash_after,
        "cash_valid": (cash_after is not None and cash_after >= 0.0),
        "status": row["status"],
        "source_scan_id": row.get("source_scan_id"),
        "created_at": row["created_at"],
        "expires_at": row["expires_at"],
        "resolved_at": row.get("resolved_at"),
    }


class RecommendationService:
    def __init__(self, *, settings: Settings, trade_service: TradeService) -> None:
        self._settings = settings
        self._trade = trade_service

    async def upsert_from_candidate(
        self,
        *,
        source_scan_id: str,
        candidate: dict[str, Any],
        cash_usd: float,
    ) -> dict[str, Any] | None:
        tp = candidate.get("trade_plan") or {}
        market = candidate.get("market") or {}

        ticker = str(candidate.get("ticker") or "").strip().upper()
        if not ticker:
            return None

        entry = float(tp.get("entry_price") or market.get("last_close") or 0.0)
        atr14 = market.get("atr14")
        atr = float(atr14) if isinstance(atr14, (int, float)) else None
        stop = float(tp.get("stop_loss") or 0.0) if tp.get("stop_loss") is not None else None
        if stop is None:
            if atr is None or atr <= 0:
                return None
            stop = entry - (atr * float(self._settings.stop_atr_multiple))

        if entry <= 0 or stop <= 0:
            return None

        stop_dist = entry - stop
        if stop_dist <= 0:
            return None

        signals = candidate.get("signals") or {}
        trend = signals.get("trend")
        mr = signals.get("mean_reversion")
        mom = signals.get("momentum_score")
        if mr == "oversold":
            strategy = "reversion"
        elif trend == "bullish" and isinstance(mom, (int, float)) and float(mom) > 0:
            strategy = "momentum"
        else:
            strategy = "manual"

        take_profit = entry + (2.0 * stop_dist)

        planning_cash = float(max(0.0, cash_usd))
        if planning_cash <= 0:
            planning_cash = float(max(0.0, self._settings.initial_budget))

        risk_pct = float(max(0.0, min(0.05, self._settings.risk_per_trade_pct)))
        risk_budget = planning_cash * risk_pct
        risk_per_share = stop_dist
        shares_by_risk = int(risk_budget // risk_per_share) if risk_per_share > 0 else 0
        shares_by_min_pos = int(math.ceil(float(self._settings.min_position_usd) / entry))
        shares_suggested = int(max(1, shares_by_risk, shares_by_min_pos, int(tp.get("shares") or 0)))

        notional = entry * shares_suggested
        max_loss = shares_suggested * stop_dist
        rr = (take_profit - entry) / stop_dist if stop_dist > 0 else None
        cash_after = float(cash_usd) - notional

        now = _utcnow()
        created_at = now.isoformat()
        expires_at = (now + timedelta(hours=int(self._settings.rec_expiry_hours))).isoformat()
        reasons = candidate.get("reasons") or []
        score = candidate.get("score")

        async with aiosqlite.connect(self._settings.db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                """
                SELECT rec_id
                FROM recommendations
                WHERE ticker = ? AND status = 'active' AND expires_at > ?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (ticker, _utcnow_iso()),
            )
            existing = await cur.fetchone()

            if existing:
                rec_id = str(existing["rec_id"])
                await db.execute(
                    """
                    UPDATE recommendations
                    SET strategy=?,
                        score=?,
                        reasons=?,
                        entry_price=?,
                        stop_loss=?,
                        take_profit=?,
                        shares=?,
                        notional_usd=?,
                        max_loss_usd=?,
                        risk_reward_ratio=?,
                        cash_after=?,
                        source_scan_id=?,
                        expires_at=?
                    WHERE rec_id=?
                    """,
                    (
                        strategy,
                        float(score) if isinstance(score, (int, float)) else None,
                        json.dumps(reasons),
                        entry,
                        stop,
                        take_profit,
                        shares_suggested,
                        notional,
                        max_loss,
                        rr,
                        cash_after,
                        source_scan_id,
                        expires_at,
                        rec_id,
                    ),
                )
            else:
                rec_id = str(uuid4())
                await db.execute(
                    """
                    INSERT INTO recommendations(
                      rec_id, ticker, strategy, score, reasons,
                      entry_price, stop_loss, take_profit, shares, notional_usd,
                      max_loss_usd, risk_reward_ratio, cash_after, status,
                      source_scan_id, created_at, expires_at
                    )
                    VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?)
                    """,
                    (
                        rec_id,
                        ticker,
                        strategy,
                        float(score) if isinstance(score, (int, float)) else None,
                        json.dumps(reasons),
                        entry,
                        stop,
                        take_profit,
                        shares_suggested,
                        notional,
                        max_loss,
                        rr,
                        cash_after,
                        source_scan_id,
                        created_at,
                        expires_at,
                    ),
                )
            await db.commit()

            cur = await db.execute("SELECT * FROM recommendations WHERE rec_id = ?", (rec_id,))
            row = await cur.fetchone()
            return _row_to_rec(dict(row)) if row else None

    async def expire_due(self) -> int:
        now = _utcnow_iso()
        async with aiosqlite.connect(self._settings.db_path) as db:
            cur = await db.execute(
                """
                UPDATE recommendations
                SET status = 'expired', resolved_at = ?
                WHERE status = 'active' AND expires_at <= ?
                """,
                (now, now),
            )
            await db.commit()
            return int(cur.rowcount or 0)

    async def list(self, *, status: str = "active", limit: int = 50) -> list[dict[str, Any]]:
        st = str(status or "active").lower()
        lim = int(max(1, min(500, limit)))
        now = _utcnow_iso()

        where = "WHERE 1=1"
        params: list[Any] = []
        if st != "all":
            where += " AND status = ?"
            params.append(st)
        if st == "active":
            where += " AND expires_at > ?"
            params.append(now)
        params.append(lim)

        async with aiosqlite.connect(self._settings.db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                f"SELECT * FROM recommendations {where} ORDER BY created_at DESC LIMIT ?",  # noqa: S608
                tuple(params),
            )
            rows = await cur.fetchall()
            return [_row_to_rec(dict(r)) for r in rows]

    async def get(self, *, rec_id: str) -> dict[str, Any] | None:
        rid = str(rec_id)
        if not rid:
            return None
        async with aiosqlite.connect(self._settings.db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute("SELECT * FROM recommendations WHERE rec_id = ?", (rid,))
            row = await cur.fetchone()
            return _row_to_rec(dict(row)) if row else None

    async def skip(self, *, rec_id: str) -> dict[str, Any]:
        rid = str(rec_id)
        now = _utcnow_iso()
        async with aiosqlite.connect(self._settings.db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute("SELECT * FROM recommendations WHERE rec_id = ?", (rid,))
            row = await cur.fetchone()
            if not row:
                raise KeyError("rec_not_found")
            if row["status"] != "active":
                return _row_to_rec(dict(row))

            await db.execute(
                "UPDATE recommendations SET status='skipped', resolved_at=? WHERE rec_id = ?",
                (now, rid),
            )
            await db.commit()
            cur = await db.execute("SELECT * FROM recommendations WHERE rec_id = ?", (rid,))
            updated = await cur.fetchone()
            if not updated:
                raise RuntimeError("rec_skip_failed")
            return _row_to_rec(dict(updated))

    async def execute(self, *, rec_id: str, override: dict[str, Any] | None = None) -> dict[str, Any]:
        rid = str(rec_id)
        now = _utcnow_iso()
        override = override or {}

        async with aiosqlite.connect(self._settings.db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute("SELECT * FROM recommendations WHERE rec_id = ?", (rid,))
            row = await cur.fetchone()
            if not row:
                raise KeyError("rec_not_found")
            rec = dict(row)

        exp = _parse_dt(rec.get("expires_at"))
        if exp and exp <= _utcnow():
            raise ValueError("rec_expired")
        if rec.get("status") != "active":
            raise ValueError("rec_not_active")

        entry_price = float(override.get("entry_price") or rec.get("entry_price") or 0.0)
        shares = float(override.get("shares") or rec.get("shares") or 0.0)
        stop_loss = rec.get("stop_loss")
        take_profit = rec.get("take_profit")
        ticker = rec.get("ticker")
        reason = "; ".join(json.loads(rec.get("reasons") or "[]")[:5])
        source_scan_id = rec.get("source_scan_id")

        trade = await self._trade.execute(
            ticker=ticker,
            entry_price=entry_price,
            shares=shares,
            stop_loss=stop_loss,
            take_profit=take_profit,
            strategy=rec.get("strategy"),
            reason=reason,
            source_scan_id=source_scan_id,
        )

        async with aiosqlite.connect(self._settings.db_path) as db:
            await db.execute(
                "UPDATE recommendations SET status='executed', resolved_at=? WHERE rec_id=?",
                (now, rid),
            )
            await db.commit()

        return {"recommendation_id": rid, "trade": trade}

