from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any

import aiosqlite
import httpx

from invest_scan.agents.market_data_agent import MarketDataAgent
from invest_scan.services.portfolio_service import PortfolioService
from invest_scan.settings import Settings


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    return datetime.fromisoformat(s)


def _safe_date(dt: datetime | None) -> date | None:
    return dt.date() if dt else None


@dataclass(frozen=True)
class JournalExport:
    filename: str
    content_type: str
    data: bytes


class JournalService:
    def __init__(
        self,
        *,
        settings: Settings,
        http: httpx.AsyncClient,
        portfolio: PortfolioService,
    ) -> None:
        self._settings = settings
        self._portfolio = portfolio
        self._market = MarketDataAgent(http, finnhub_api_key=settings.finnhub_api_key)

    async def summary(self) -> dict[str, Any]:
        initial_budget = float(self._settings.initial_budget)
        p = await self._portfolio.get_portfolio()
        cash = float(p.cash_usd or 0.0)

        # Price open positions using last_close (fallback to avg_price).
        positions_value = 0.0
        pos_list = list(p.positions or [])
        pos_tickers = [
            str(pos.get("ticker") or "").strip().upper()
            for pos in pos_list
            if str(pos.get("ticker") or "").strip()
        ]
        pos_tickers = list(dict.fromkeys([t for t in pos_tickers if t]))
        histories: dict[str, Any] = {}
        if pos_tickers:
            try:
                histories, _src = await self._market.fetch_histories(
                    pos_tickers, period="30d", attempts=2, backoff_seconds=1.0
                )
            except Exception:
                histories = {}

        last_close_by_ticker: dict[str, float] = {}
        for t in pos_tickers:
            pts = histories.get(t) or []
            if pts:
                last_close_by_ticker[t] = float(pts[-1].close)

        for pos in pos_list:
            t = str(pos.get("ticker") or "").strip().upper()
            qty = float(pos.get("quantity") or 0.0)
            if not t or qty <= 0:
                continue
            px = float(last_close_by_ticker.get(t) or 0.0)
            if px > 0:
                positions_value += qty * px
            else:
                avg = float(pos.get("avg_price") or 0.0)
                positions_value += qty * avg

        async with aiosqlite.connect(self._settings.db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute("SELECT * FROM trades ORDER BY created_at DESC")
            trades = [dict(r) for r in await cur.fetchall()]

        total_trades = len(trades)
        open_trades = sum(1 for t in trades if t.get("status") == "open")
        closed = [t for t in trades if t.get("status") == "closed"]
        closed_trades = len(closed)

        pnls = [float(t["realised_pnl"]) for t in closed if t.get("realised_pnl") is not None]
        winners = [pnl for pnl in pnls if pnl > 0]
        losers = [pnl for pnl in pnls if pnl < 0]

        def avg(xs: list[float]) -> float:
            return float(sum(xs) / len(xs)) if xs else 0.0

        holding_days = [
            int(t["holding_days"]) for t in closed if t.get("holding_days") is not None
        ]

        best_trade = None
        worst_trade = None
        if pnls:
            best = max((t for t in closed if t.get("realised_pnl") is not None), key=lambda x: float(x["realised_pnl"]))
            worst = min((t for t in closed if t.get("realised_pnl") is not None), key=lambda x: float(x["realised_pnl"]))
            best_trade = {"ticker": best.get("ticker"), "pnl": float(best.get("realised_pnl") or 0.0)}
            worst_trade = {"ticker": worst.get("ticker"), "pnl": float(worst.get("realised_pnl") or 0.0)}

        total_value = cash + positions_value
        total_pnl = total_value - initial_budget
        total_pnl_pct = (total_pnl / initial_budget * 100.0) if initial_budget > 0 else 0.0

        loss_warning = None
        loss_critical = None
        if initial_budget > 0:
            ratio = total_pnl / initial_budget
            if ratio < -0.50:
                loss_critical = "You have lost 50% of your initial budget"
            elif ratio < -0.25:
                loss_warning = "You have lost 25% of your initial budget"

        win_rate = (len(winners) / closed_trades * 100.0) if closed_trades else 0.0

        return {
            "initial_budget": initial_budget,
            "current_cash": cash,
            "open_positions_value": positions_value,
            "total_value": total_value,
            "total_pnl": total_pnl,
            "total_pnl_pct": total_pnl_pct,
            "total_trades": total_trades,
            "open_trades": open_trades,
            "closed_trades": closed_trades,
            "winners": len(winners),
            "losers": len(losers),
            "win_rate": win_rate,
            "avg_winner_pnl": avg(winners),
            "avg_loser_pnl": avg(losers),
            "avg_holding_days": avg([float(x) for x in holding_days]),
            "best_trade": best_trade,
            "worst_trade": worst_trade,
            "loss_warning": loss_warning,
            "loss_critical": loss_critical,
        }

    async def export_closed_csv(self) -> JournalExport:
        async with aiosqlite.connect(self._settings.db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                """
                SELECT *
                FROM trades
                WHERE status = 'closed'
                ORDER BY exit_date DESC, created_at DESC
                """
            )
            rows = [dict(r) for r in await cur.fetchall()]

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(
            [
                "trade_id",
                "ticker",
                "strategy",
                "entry_date",
                "entry_price",
                "exit_date",
                "exit_price",
                "shares",
                "realised_pnl",
                "holding_days",
                "exit_reason",
            ]
        )
        for r in rows:
            writer.writerow(
                [
                    r.get("trade_id"),
                    r.get("ticker"),
                    r.get("strategy") or "",
                    r.get("entry_date"),
                    r.get("entry_price"),
                    r.get("exit_date") or "",
                    r.get("exit_price") or "",
                    r.get("shares"),
                    r.get("realised_pnl") if r.get("realised_pnl") is not None else "",
                    r.get("holding_days") if r.get("holding_days") is not None else "",
                    r.get("exit_reason") or "",
                ]
            )

        data = output.getvalue().encode("utf-8")
        filename = f"journal_closed_trades_{_utcnow().date().isoformat()}.csv"
        return JournalExport(filename=filename, content_type="text/csv; charset=utf-8", data=data)

    async def daily(self, *, max_days: int = 180) -> dict[str, Any]:
        async with aiosqlite.connect(self._settings.db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute("SELECT * FROM trades ORDER BY entry_date ASC")
            trades = [dict(r) for r in await cur.fetchall()]

        today = _utcnow().date()
        start_dates = [_safe_date(_parse_dt(t.get("entry_date"))) for t in trades]
        start = min([d for d in start_dates if d is not None], default=today)

        days_total = (today - start).days + 1
        if days_total > max_days:
            start = today.fromordinal(today.toordinal() - max_days + 1)

        initial_budget = float(self._settings.initial_budget)
        snapshots: list[dict[str, Any]] = []
        for i in range((today - start).days + 1):
            d = start.fromordinal(start.toordinal() + i)
            cash = initial_budget
            positions_value = 0.0

            for t in trades:
                entry_dt = _parse_dt(t.get("entry_date"))
                entry_d = entry_dt.date() if entry_dt else None
                if entry_d and entry_d <= d:
                    cash -= float(t.get("cost_basis") or 0.0)

                exit_dt = _parse_dt(t.get("exit_date"))
                exit_d = exit_dt.date() if exit_dt else None
                if exit_d and exit_d <= d:
                    cash += float(t.get("exit_price") or 0.0) * float(t.get("shares") or 0.0)

                is_open_on_day = entry_d is not None and entry_d <= d and (exit_d is None or exit_d > d)
                if is_open_on_day:
                    positions_value += float(t.get("entry_price") or 0.0) * float(t.get("shares") or 0.0)

            snapshots.append(
                {
                    "date": d.isoformat(),
                    "total_value": cash + positions_value,
                    "cash": cash,
                    "positions_value": positions_value,
                }
            )

        return {"snapshots": snapshots}

