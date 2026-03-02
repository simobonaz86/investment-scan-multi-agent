from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import aiosqlite

from invest_scan.settings import Settings


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class IntradayConfig:
    enabled: bool
    only_market_hours: bool
    interval: str
    period: str
    watchlist_size: int
    poll_seconds: int
    updated_at: str | None = None


@dataclass(frozen=True)
class PortfolioConfig:
    total_portfolio_usd: float
    sleeve_pct: float
    max_positions: int
    risk_per_trade_pct: float
    max_position_pct: float
    updated_at: str | None = None


def _coerce_bool(x: Any) -> bool | None:
    if isinstance(x, bool):
        return x
    if isinstance(x, (int, float)) and x in (0, 1):
        return bool(int(x))
    if isinstance(x, str):
        v = x.strip().lower()
        if v in {"true", "1", "yes", "y", "on"}:
            return True
        if v in {"false", "0", "no", "n", "off"}:
            return False
    return None


class ConfigService:
    def __init__(self, *, settings: Settings) -> None:
        self._settings = settings

    async def apply_runtime_overrides(self) -> None:
        cfg = await self.get_intraday_config()
        if cfg is not None:
            self._settings.intraday_enabled = bool(cfg.enabled)
            self._settings.intraday_only_market_hours = bool(cfg.only_market_hours)
            self._settings.intraday_interval = str(cfg.interval)
            self._settings.intraday_period = str(cfg.period)
            self._settings.intraday_watchlist_size = int(cfg.watchlist_size)
            self._settings.intraday_poll_seconds = int(cfg.poll_seconds)

        pcfg = await self.get_portfolio_config()
        if pcfg is not None:
            self._settings.total_portfolio_usd = float(pcfg.total_portfolio_usd)
            self._settings.tactical_sleeve_pct = float(pcfg.sleeve_pct)
            self._settings.tactical_max_positions = int(pcfg.max_positions)
            self._settings.tactical_risk_per_trade_pct = float(pcfg.risk_per_trade_pct)
            self._settings.tactical_max_position_pct = float(pcfg.max_position_pct)

    async def get_intraday_config(self) -> IntradayConfig | None:
        async with aiosqlite.connect(self._settings.db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute("SELECT * FROM app_config WHERE key = ?", ("intraday",))
            row = await cur.fetchone()
            if not row:
                return None
            try:
                payload = json.loads(row["value_json"])
            except Exception:
                payload = {}

        return IntradayConfig(
            enabled=bool(payload.get("enabled")),
            only_market_hours=bool(payload.get("only_market_hours")),
            interval=str(payload.get("interval") or "15m"),
            period=str(payload.get("period") or "5d"),
            watchlist_size=int(payload.get("watchlist_size") or 20),
            poll_seconds=int(payload.get("poll_seconds") or 180),
            updated_at=str(row["updated_at"]),
        )

    async def set_intraday_config(self, *, patch: dict[str, Any]) -> IntradayConfig:
        # Start from current settings (which include env defaults + any applied overrides).
        enabled = bool(self._settings.intraday_enabled)
        only_mh = bool(self._settings.intraday_only_market_hours)
        interval = str(self._settings.intraday_interval or "15m")
        period = str(self._settings.intraday_period or "5d")
        watchlist_size = int(self._settings.intraday_watchlist_size or 20)
        poll_seconds = int(self._settings.intraday_poll_seconds or 180)

        if "enabled" in patch:
            v = _coerce_bool(patch.get("enabled"))
            if v is not None:
                enabled = bool(v)
        if "only_market_hours" in patch:
            v = _coerce_bool(patch.get("only_market_hours"))
            if v is not None:
                only_mh = bool(v)
        if "interval" in patch:
            interval = str(patch.get("interval") or interval).strip()
        if "period" in patch:
            period = str(patch.get("period") or period).strip()
        if "watchlist_size" in patch:
            try:
                watchlist_size = int(patch.get("watchlist_size"))
            except Exception:
                pass
        if "poll_seconds" in patch:
            try:
                poll_seconds = int(patch.get("poll_seconds"))
            except Exception:
                pass

        # Basic validation / clamping.
        if interval not in {"5m", "15m", "30m", "60m"}:
            interval = "15m"
        if watchlist_size < 5:
            watchlist_size = 5
        if watchlist_size > 50:
            watchlist_size = 50
        if poll_seconds < 30:
            poll_seconds = 30
        if poll_seconds > 3600:
            poll_seconds = 3600
        if not period:
            period = "5d"

        payload = {
            "enabled": enabled,
            "only_market_hours": only_mh,
            "interval": interval,
            "period": period,
            "watchlist_size": watchlist_size,
            "poll_seconds": poll_seconds,
        }
        now = _utcnow_iso()
        async with aiosqlite.connect(self._settings.db_path) as db:
            await db.execute(
                """
                INSERT INTO app_config(key, value_json, updated_at)
                VALUES(?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                  value_json=excluded.value_json,
                  updated_at=excluded.updated_at
                """,
                ("intraday", json.dumps(payload), now),
            )
            await db.commit()

        # Apply live.
        self._settings.intraday_enabled = enabled
        self._settings.intraday_only_market_hours = only_mh
        self._settings.intraday_interval = interval
        self._settings.intraday_period = period
        self._settings.intraday_watchlist_size = watchlist_size
        self._settings.intraday_poll_seconds = poll_seconds

        return IntradayConfig(**payload, updated_at=now)

    async def get_portfolio_config(self) -> PortfolioConfig | None:
        async with aiosqlite.connect(self._settings.db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute("SELECT * FROM app_config WHERE key = ?", ("portfolio",))
            row = await cur.fetchone()
            if not row:
                return None
            try:
                payload = json.loads(row["value_json"])
            except Exception:
                payload = {}

        return PortfolioConfig(
            total_portfolio_usd=float(payload.get("total_portfolio_usd") or 0.0),
            sleeve_pct=float(payload.get("sleeve_pct") or 0.01),
            max_positions=int(payload.get("max_positions") or 4),
            risk_per_trade_pct=float(payload.get("risk_per_trade_pct") or 0.01),
            max_position_pct=float(payload.get("max_position_pct") or 0.35),
            updated_at=str(row["updated_at"]),
        )

    async def set_portfolio_config(self, *, patch: dict[str, Any]) -> PortfolioConfig:
        total = float(self._settings.total_portfolio_usd or 0.0)
        sleeve_pct = float(self._settings.tactical_sleeve_pct or 0.01)
        max_positions = int(self._settings.tactical_max_positions or 4)
        risk_pct = float(self._settings.tactical_risk_per_trade_pct or 0.01)
        max_pos_pct = float(self._settings.tactical_max_position_pct or 0.35)

        if "total_portfolio_usd" in patch:
            try:
                total = float(patch.get("total_portfolio_usd") or 0.0)
            except Exception:
                pass
        if "sleeve_pct" in patch:
            try:
                sleeve_pct = float(patch.get("sleeve_pct"))
            except Exception:
                pass
        if "max_positions" in patch:
            try:
                max_positions = int(patch.get("max_positions"))
            except Exception:
                pass
        if "risk_per_trade_pct" in patch:
            try:
                risk_pct = float(patch.get("risk_per_trade_pct"))
            except Exception:
                pass
        if "max_position_pct" in patch:
            try:
                max_pos_pct = float(patch.get("max_position_pct"))
            except Exception:
                pass

        total = float(max(0.0, total))
        sleeve_pct = float(max(0.0, min(0.25, sleeve_pct)))
        max_positions = int(max(1, min(20, max_positions)))
        risk_pct = float(max(0.001, min(0.05, risk_pct)))
        max_pos_pct = float(max(0.05, min(1.0, max_pos_pct)))

        payload = {
            "total_portfolio_usd": total,
            "sleeve_pct": sleeve_pct,
            "max_positions": max_positions,
            "risk_per_trade_pct": risk_pct,
            "max_position_pct": max_pos_pct,
        }
        now = _utcnow_iso()
        async with aiosqlite.connect(self._settings.db_path) as db:
            await db.execute(
                """
                INSERT INTO app_config(key, value_json, updated_at)
                VALUES(?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                  value_json=excluded.value_json,
                  updated_at=excluded.updated_at
                """,
                ("portfolio", json.dumps(payload), now),
            )
            await db.commit()

        self._settings.total_portfolio_usd = total
        self._settings.tactical_sleeve_pct = sleeve_pct
        self._settings.tactical_max_positions = max_positions
        self._settings.tactical_risk_per_trade_pct = risk_pct
        self._settings.tactical_max_position_pct = max_pos_pct

        return PortfolioConfig(**payload, updated_at=now)

