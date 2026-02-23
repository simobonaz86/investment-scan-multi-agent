from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

from fastapi import FastAPI

from invest_scan import db
from invest_scan.settings import Settings


def _parse_tickers(csv_str: str) -> list[str]:
    parts = [p.strip().upper() for p in (csv_str or "").split(",")]
    return [p for p in parts if p]


def _parse_hhmm(s: str) -> time:
    hh, mm = (s or "").split(":")
    return time(hour=int(hh), minute=int(mm))


@dataclass(frozen=True)
class MarketHours:
    tz: ZoneInfo
    open_time: time
    close_time: time

    def is_open_now(self) -> bool:
        now = datetime.now(self.tz)
        if now.weekday() >= 5:
            return False
        t = now.time()
        return self.open_time <= t <= self.close_time


def _market_hours(settings: Settings) -> MarketHours:
    return MarketHours(
        tz=ZoneInfo(settings.market_timezone),
        open_time=_parse_hhmm(settings.market_open_hhmm),
        close_time=_parse_hhmm(settings.market_close_hhmm),
    )


async def autoscan_loop(app: FastAPI) -> None:
    settings: Settings = app.state.settings
    if not settings.autoscan_enabled:
        return

    mh = _market_hours(settings)
    interval = max(30, int(settings.autoscan_interval_seconds))

    while True:
        try:
            try:
                if hasattr(app.state, "recommendation_service"):
                    await app.state.recommendation_service.expire_due()
            except Exception:
                pass
            if (not settings.autoscan_only_market_hours) or mh.is_open_now():
                latest = await db.get_latest_scan(settings.db_path)
                if latest and latest.get("status") in {"queued", "running"}:
                    await asyncio.sleep(interval)
                    continue

                tickers = _parse_tickers(settings.autoscan_tickers_csv)[:30]
                if tickers:
                    payload = {"tickers": tickers, "as_of": "auto", "source": "autoscan"}
                    scan_id = await db.create_scan(settings.db_path, payload)
                    asyncio.create_task(
                        app.state.scan_service.run_and_persist(scan_id=scan_id, request=payload)
                    )

            await asyncio.sleep(interval)
        except asyncio.CancelledError:
            raise
        except Exception:
            # MVP: avoid crashing the app due to scheduler errors.
            await asyncio.sleep(min(30, interval))


async def market_scan_loop(app: FastAPI) -> None:
    settings: Settings = app.state.settings
    if not settings.marketscan_enabled:
        return

    mh = _market_hours(settings)
    interval = max(60, int(settings.marketscan_interval_seconds))

    while True:
        try:
            try:
                if hasattr(app.state, "recommendation_service"):
                    await app.state.recommendation_service.expire_due()
            except Exception:
                pass
            if (not settings.marketscan_only_market_hours) or mh.is_open_now():
                latest = await db.get_latest_market_scan(settings.db_path)
                if latest and latest.get("status") in {"queued", "running"}:
                    await asyncio.sleep(interval)
                    continue

                scan_id = await db.create_market_scan(settings.db_path)
                asyncio.create_task(app.state.market_scan_service.run_and_persist(scan_id=scan_id))

            await asyncio.sleep(interval)
        except asyncio.CancelledError:
            raise
        except Exception:
            await asyncio.sleep(min(30, interval))
