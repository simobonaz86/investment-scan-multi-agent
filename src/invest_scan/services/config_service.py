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
        if cfg is None:
            return
        self._settings.intraday_enabled = bool(cfg.enabled)
        self._settings.intraday_only_market_hours = bool(cfg.only_market_hours)
        self._settings.intraday_interval = str(cfg.interval)
        self._settings.intraday_period = str(cfg.period)
        self._settings.intraday_watchlist_size = int(cfg.watchlist_size)
        self._settings.intraday_poll_seconds = int(cfg.poll_seconds)

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

