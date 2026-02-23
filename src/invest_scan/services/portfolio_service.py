from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import aiosqlite

from invest_scan.settings import Settings


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _norm_header(s: str) -> str:
    return "".join(ch.lower() for ch in (s or "") if ch.isalnum())


@dataclass(frozen=True)
class Portfolio:
    account_id: str
    cash_usd: float
    positions: list[dict[str, Any]]


class PortfolioService:
    def __init__(self, *, settings: Settings) -> None:
        self._settings = settings
        self._account_id = "default"

    async def get_portfolio(self) -> Portfolio:
        async with aiosqlite.connect(self._settings.db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT cash_usd FROM portfolio_account WHERE account_id = ?",
                (self._account_id,),
            )
            row = await cur.fetchone()
            cash = float(row["cash_usd"]) if row else 0.0

            cur = await db.execute(
                """
                SELECT ticker, quantity, avg_price, updated_at
                FROM portfolio_position
                WHERE account_id = ?
                ORDER BY ticker
                """,
                (self._account_id,),
            )
            pos_rows = await cur.fetchall()
            positions = [dict(r) for r in pos_rows]
            return Portfolio(account_id=self._account_id, cash_usd=cash, positions=positions)

    async def set_cash_usd(self, cash_usd: float) -> None:
        cash = float(max(0.0, cash_usd))
        async with aiosqlite.connect(self._settings.db_path) as db:
            await db.execute(
                """
                INSERT INTO portfolio_account(account_id, cash_usd, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(account_id) DO UPDATE SET cash_usd=excluded.cash_usd, updated_at=excluded.updated_at
                """,
                (self._account_id, cash, _utcnow_iso()),
            )
            await db.commit()

    async def upsert_positions(self, positions: list[dict[str, Any]]) -> None:
        now = _utcnow_iso()
        async with aiosqlite.connect(self._settings.db_path) as db:
            for p in positions:
                ticker = str(p["ticker"]).strip().upper()
                qty = float(p.get("quantity") or 0.0)
                avg = p.get("avg_price")
                avg_f = float(avg) if avg is not None else None
                await db.execute(
                    """
                    INSERT INTO portfolio_position(account_id, ticker, quantity, avg_price, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(account_id, ticker) DO UPDATE SET
                      quantity=excluded.quantity,
                      avg_price=excluded.avg_price,
                      updated_at=excluded.updated_at
                    """,
                    (self._account_id, ticker, qty, avg_f, now),
                )
            await db.commit()

    def parse_revolut_csv(self, content: bytes, *, mode: str = "auto") -> dict[str, Any]:
        text = content.decode("utf-8-sig", errors="replace")
        reader = csv.DictReader(io.StringIO(text))
        if not reader.fieldnames:
            raise ValueError("csv_missing_headers")

        headers = {_norm_header(h): h for h in reader.fieldnames}

        def has(*candidates: str) -> bool:
            return any(_norm_header(c) in headers for c in candidates)

        # Mode detection:
        # - trades: Date, Instrument, Type, Quantity, Price, Total (common Revolut trade export)
        # - positions: Instrument/Ticker, Quantity, Avg/Average Price
        if mode == "auto":
            if has("type") and has("price") and has("quantity"):
                mode = "trades"
            else:
                mode = "positions"

        rows = list(reader)
        if mode == "positions":
            inst_key = headers.get("ticker") or headers.get("symbol") or headers.get("instrument")
            qty_key = headers.get("quantity") or headers.get("qty")
            avg_key = headers.get("averageprice") or headers.get("avgprice") or headers.get("price")
            if not inst_key or not qty_key:
                raise ValueError("positions_csv_requires_instrument_and_quantity")

            positions: list[dict[str, Any]] = []
            for r in rows:
                t = (r.get(inst_key) or "").strip().upper()
                if not t:
                    continue
                try:
                    qty = float((r.get(qty_key) or "0").replace(",", ""))
                except Exception:
                    continue
                avg = None
                if avg_key and r.get(avg_key):
                    try:
                        avg = float((r.get(avg_key) or "").replace(",", ""))
                    except Exception:
                        avg = None
                positions.append({"ticker": t, "quantity": qty, "avg_price": avg})
            return {"mode": "positions", "positions": positions, "trades_imported": 0}

        if mode == "trades":
            date_key = headers.get("date")
            inst_key = headers.get("instrument") or headers.get("ticker") or headers.get("symbol")
            side_key = headers.get("type") or headers.get("side")
            qty_key = headers.get("quantity") or headers.get("qty")
            price_key = headers.get("price")
            total_key = headers.get("total") or headers.get("amount")

            if not inst_key or not side_key or not qty_key:
                raise ValueError("trades_csv_requires_instrument_type_quantity")

            trades: list[dict[str, Any]] = []
            for r in rows:
                inst = (r.get(inst_key) or "").strip().upper()
                side = (r.get(side_key) or "").strip().upper()
                if side not in {"BUY", "SELL"} or not inst:
                    continue
                try:
                    qty = float((r.get(qty_key) or "0").replace(",", ""))
                except Exception:
                    continue
                price = None
                if price_key and r.get(price_key):
                    try:
                        price = float((r.get(price_key) or "").replace(",", ""))
                    except Exception:
                        price = None
                total = None
                if total_key and r.get(total_key):
                    try:
                        total = float((r.get(total_key) or "").replace(",", ""))
                    except Exception:
                        total = None
                trade_date = (r.get(date_key) or "").strip() if date_key else None
                trades.append(
                    {
                        "trade_date": trade_date,
                        "instrument": inst,
                        "side": side,
                        "quantity": qty,
                        "price": price,
                        "total": total,
                    }
                )

            positions = _positions_from_trades(trades)
            return {"mode": "trades", "positions": positions, "trades_imported": len(trades)}

        raise ValueError("invalid_mode")

    async def import_revolut_csv(self, content: bytes, *, mode: str = "auto") -> dict[str, Any]:
        parsed = self.parse_revolut_csv(content, mode=mode)
        await self.upsert_positions(parsed["positions"])
        return parsed


def _positions_from_trades(trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # Running average cost. SELLs reduce at current avg cost.
    by_inst: dict[str, dict[str, float]] = {}
    for t in trades:
        inst = t["instrument"]
        side = t["side"]
        qty = float(t["quantity"])
        price = t.get("price")
        if price is None:
            continue
        price = float(price)

        st = by_inst.setdefault(inst, {"qty": 0.0, "cost": 0.0})
        if side == "BUY":
            st["qty"] += qty
            st["cost"] += qty * price
        elif side == "SELL":
            if st["qty"] <= 0:
                continue
            avg = st["cost"] / st["qty"] if st["qty"] > 0 else 0.0
            sell_qty = min(qty, st["qty"])
            st["qty"] -= sell_qty
            st["cost"] -= sell_qty * avg

    positions: list[dict[str, Any]] = []
    for inst, st in by_inst.items():
        qty = st["qty"]
        if qty <= 0:
            continue
        avg = st["cost"] / qty if qty > 0 else None
        positions.append({"ticker": inst, "quantity": qty, "avg_price": avg})
    positions.sort(key=lambda p: p["ticker"])
    return positions

