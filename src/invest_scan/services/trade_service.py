from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

import aiosqlite

from invest_scan.settings import Settings


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _utcnow_iso() -> str:
    return _utcnow().isoformat()


def _parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    return datetime.fromisoformat(s)


def _row_to_trade(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "trade_id": row["trade_id"],
        "account_id": row["account_id"],
        "ticker": row["ticker"],
        "direction": row["direction"],
        "strategy": row.get("strategy"),
        "status": row["status"],
        "entry_price": float(row["entry_price"]),
        "entry_date": _parse_dt(row["entry_date"]),
        "shares": float(row["shares"]),
        "cost_basis": float(row["cost_basis"]),
        "stop_loss": float(row["stop_loss"]) if row.get("stop_loss") is not None else None,
        "take_profit": float(row["take_profit"]) if row.get("take_profit") is not None else None,
        "reason": row.get("reason"),
        "exit_price": float(row["exit_price"]) if row.get("exit_price") is not None else None,
        "exit_date": _parse_dt(row.get("exit_date")),
        "exit_reason": row.get("exit_reason"),
        "realised_pnl": float(row["realised_pnl"]) if row.get("realised_pnl") is not None else None,
        "holding_days": int(row["holding_days"]) if row.get("holding_days") is not None else None,
        "source_scan_id": row.get("source_scan_id"),
        "created_at": _parse_dt(row["created_at"]),
        "updated_at": _parse_dt(row["updated_at"]),
    }


@dataclass(frozen=True)
class ExecuteResult:
    trade: dict[str, Any]


class TradeService:
    def __init__(self, *, settings: Settings, account_id: str = "default") -> None:
        self._settings = settings
        self._account_id = account_id

    async def execute(
        self,
        *,
        ticker: str,
        entry_price: float,
        shares: float,
        stop_loss: float | None = None,
        take_profit: float | None = None,
        strategy: str | None = None,
        reason: str | None = None,
        source_scan_id: str | None = None,
    ) -> dict[str, Any]:
        t = str(ticker).strip().upper()
        entry = float(entry_price)
        qty = float(shares)
        if not t:
            raise ValueError("invalid_ticker")
        if entry <= 0 or qty <= 0:
            raise ValueError("entry_price_and_shares_must_be_positive")

        now = _utcnow_iso()
        trade_id = str(uuid4())
        cost_basis = entry * qty

        async with aiosqlite.connect(self._settings.db_path) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("BEGIN IMMEDIATE")

            cur = await db.execute(
                "SELECT cash_usd FROM portfolio_account WHERE account_id = ?",
                (self._account_id,),
            )
            row = await cur.fetchone()
            cash = float(row["cash_usd"]) if row else 0.0
            if cost_basis > cash + 1e-9:
                raise ValueError(f"insufficient_cash: need ${cost_basis:.2f}, have ${cash:.2f}")

            new_cash = cash - cost_basis
            await db.execute(
                """
                INSERT INTO portfolio_account(account_id, cash_usd, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(account_id) DO UPDATE SET cash_usd=excluded.cash_usd, updated_at=excluded.updated_at
                """,
                (self._account_id, new_cash, now),
            )

            cur = await db.execute(
                """
                SELECT quantity, avg_price
                FROM portfolio_position
                WHERE account_id = ? AND ticker = ?
                """,
                (self._account_id, t),
            )
            pos = await cur.fetchone()
            if pos:
                old_qty = float(pos["quantity"])
                old_avg = float(pos["avg_price"]) if pos["avg_price"] is not None else entry
            else:
                old_qty = 0.0
                old_avg = entry
            new_qty = old_qty + qty
            new_avg = ((old_qty * old_avg) + (qty * entry)) / new_qty if new_qty > 0 else entry

            await db.execute(
                """
                INSERT INTO portfolio_position(account_id, ticker, quantity, avg_price, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(account_id, ticker) DO UPDATE SET
                  quantity=excluded.quantity,
                  avg_price=excluded.avg_price,
                  updated_at=excluded.updated_at
                """,
                (self._account_id, t, new_qty, new_avg, now),
            )

            await db.execute(
                """
                INSERT INTO trades(
                  trade_id,
                  account_id,
                  ticker,
                  direction,
                  strategy,
                  status,
                  entry_price,
                  entry_date,
                  shares,
                  cost_basis,
                  stop_loss,
                  take_profit,
                  reason,
                  source_scan_id,
                  created_at,
                  updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    trade_id,
                    self._account_id,
                    t,
                    "long",
                    strategy,
                    "open",
                    entry,
                    now,
                    qty,
                    cost_basis,
                    float(stop_loss) if stop_loss is not None else None,
                    float(take_profit) if take_profit is not None else None,
                    reason,
                    source_scan_id,
                    now,
                    now,
                ),
            )

            await db.commit()

            cur = await db.execute("SELECT * FROM trades WHERE trade_id = ?", (trade_id,))
            created = await cur.fetchone()
            if not created:
                raise RuntimeError("trade_create_failed")
            return _row_to_trade(dict(created))

    async def close(
        self,
        *,
        trade_id: str,
        exit_price: float,
        exit_reason: str | None = None,
    ) -> dict[str, Any]:
        tid = str(trade_id)
        px = float(exit_price)
        if not tid:
            raise ValueError("invalid_trade_id")
        if px <= 0:
            raise ValueError("exit_price_must_be_positive")

        now_dt = _utcnow()
        now = now_dt.isoformat()

        async with aiosqlite.connect(self._settings.db_path) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("BEGIN IMMEDIATE")

            cur = await db.execute("SELECT * FROM trades WHERE trade_id = ?", (tid,))
            row = await cur.fetchone()
            if not row:
                raise KeyError("trade_not_found")
            trade = dict(row)
            if trade["status"] != "open":
                raise ValueError("trade_not_open")

            entry_price = float(trade["entry_price"])
            shares = float(trade["shares"])
            pnl = (px - entry_price) * shares
            entry_dt = _parse_dt(trade.get("entry_date")) or now_dt
            holding_days = max(0, int((now_dt - entry_dt).total_seconds() // 86400))

            await db.execute(
                """
                UPDATE trades
                SET status = ?,
                    exit_price = ?,
                    exit_date = ?,
                    exit_reason = ?,
                    realised_pnl = ?,
                    holding_days = ?,
                    updated_at = ?
                WHERE trade_id = ?
                """,
                ("closed", px, now, exit_reason, pnl, holding_days, now, tid),
            )

            proceeds = px * shares
            cur = await db.execute(
                "SELECT cash_usd FROM portfolio_account WHERE account_id = ?",
                (self._account_id,),
            )
            cash_row = await cur.fetchone()
            cash = float(cash_row["cash_usd"]) if cash_row else 0.0
            new_cash = cash + proceeds
            await db.execute(
                """
                INSERT INTO portfolio_account(account_id, cash_usd, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(account_id) DO UPDATE SET cash_usd=excluded.cash_usd, updated_at=excluded.updated_at
                """,
                (self._account_id, new_cash, now),
            )

            ticker = str(trade["ticker"]).strip().upper()
            cur = await db.execute(
                """
                SELECT quantity, avg_price
                FROM portfolio_position
                WHERE account_id = ? AND ticker = ?
                """,
                (self._account_id, ticker),
            )
            pos = await cur.fetchone()
            if pos:
                old_qty = float(pos["quantity"])
                new_qty = old_qty - shares
                if new_qty <= 1e-9:
                    await db.execute(
                        "DELETE FROM portfolio_position WHERE account_id = ? AND ticker = ?",
                        (self._account_id, ticker),
                    )
                else:
                    await db.execute(
                        """
                        UPDATE portfolio_position
                        SET quantity = ?, updated_at = ?
                        WHERE account_id = ? AND ticker = ?
                        """,
                        (new_qty, now, self._account_id, ticker),
                    )

            await db.commit()

            cur = await db.execute("SELECT * FROM trades WHERE trade_id = ?", (tid,))
            updated = await cur.fetchone()
            if not updated:
                raise RuntimeError("trade_close_failed")
            return _row_to_trade(dict(updated))

    async def get(self, *, trade_id: str) -> dict[str, Any] | None:
        tid = str(trade_id)
        if not tid:
            return None
        async with aiosqlite.connect(self._settings.db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute("SELECT * FROM trades WHERE trade_id = ?", (tid,))
            row = await cur.fetchone()
            return _row_to_trade(dict(row)) if row else None

    async def list(
        self,
        *,
        status: Literal["open", "closed", "all"] = "all",
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        lim = int(max(1, min(500, limit)))
        where = ""
        params: tuple[Any, ...] = (lim,)
        if status in {"open", "closed"}:
            where = "WHERE status = ?"
            params = (status, lim)

        async with aiosqlite.connect(self._settings.db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                f"SELECT * FROM trades {where} ORDER BY created_at DESC LIMIT ?",  # noqa: S608
                params,
            )
            rows = await cur.fetchall()
            return [_row_to_trade(dict(r)) for r in rows]

