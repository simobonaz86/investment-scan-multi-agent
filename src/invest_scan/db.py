from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import aiosqlite


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def init_db(db_path: str) -> None:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(db_path) as db:
        # Performance + concurrency friendly defaults for a single-node SQLite app.
        # If WAL is unsupported by the filesystem, SQLite will ignore/fallback safely.
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA synchronous=NORMAL")
        await db.execute("PRAGMA temp_store=MEMORY")
        await db.execute("PRAGMA foreign_keys=ON")

        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS scans (
              scan_id TEXT PRIMARY KEY,
              created_at TEXT NOT NULL,
              status TEXT NOT NULL,
              started_at TEXT,
              finished_at TEXT,
              request_json TEXT NOT NULL,
              result_json TEXT,
              error TEXT
            )
            """
        )
        await db.execute("CREATE INDEX IF NOT EXISTS idx_scans_created_at ON scans(created_at)")

        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS portfolio_account (
              account_id TEXT PRIMARY KEY,
              cash_usd REAL NOT NULL,
              updated_at TEXT NOT NULL
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS portfolio_position (
              account_id TEXT NOT NULL,
              ticker TEXT NOT NULL,
              quantity REAL NOT NULL,
              avg_price REAL,
              updated_at TEXT NOT NULL,
              PRIMARY KEY (account_id, ticker)
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS portfolio_trade (
              trade_id INTEGER PRIMARY KEY AUTOINCREMENT,
              account_id TEXT NOT NULL,
              trade_date TEXT,
              instrument TEXT NOT NULL,
              side TEXT NOT NULL,
              quantity REAL NOT NULL,
              price REAL,
              total REAL
            )
            """
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_portfolio_trade_account_date ON portfolio_trade(account_id, trade_date)"
        )

        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS market_scans (
              scan_id TEXT PRIMARY KEY,
              created_at TEXT NOT NULL,
              status TEXT NOT NULL,
              started_at TEXT,
              finished_at TEXT,
              result_json TEXT,
              error TEXT
            )
            """
        )
        await db.execute("CREATE INDEX IF NOT EXISTS idx_market_scans_created_at ON market_scans(created_at)")

        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS trades (
                trade_id TEXT PRIMARY KEY,
                account_id TEXT NOT NULL DEFAULT 'default',
                ticker TEXT NOT NULL,
                direction TEXT NOT NULL DEFAULT 'long',
                strategy TEXT,
                status TEXT NOT NULL DEFAULT 'open',
                entry_price REAL NOT NULL,
                entry_date TEXT NOT NULL,
                shares REAL NOT NULL,
                cost_basis REAL NOT NULL,
                stop_loss REAL,
                take_profit REAL,
                reason TEXT,
                exit_price REAL,
                exit_date TEXT,
                exit_reason TEXT,
                realised_pnl REAL,
                holding_days INTEGER,
                source_scan_id TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        await db.execute("CREATE INDEX IF NOT EXISTS idx_trades_created_at ON trades(created_at)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_trades_status ON trades(status)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_trades_ticker ON trades(ticker)")

        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS recommendations (
                rec_id TEXT PRIMARY KEY,
                ticker TEXT NOT NULL,
                strategy TEXT,
                score REAL,
                reasons TEXT,
                entry_price REAL,
                stop_loss REAL,
                take_profit REAL,
                shares INTEGER,
                notional_usd REAL,
                max_loss_usd REAL,
                risk_reward_ratio REAL,
                cash_after REAL,
                status TEXT NOT NULL DEFAULT 'active',
                source_scan_id TEXT,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                resolved_at TEXT
            )
            """
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_recommendations_status_expires ON recommendations(status, expires_at)"
        )
        await db.commit()


async def create_scan(db_path: str, request: dict[str, Any]) -> UUID:
    scan_id = uuid4()
    created_at = _utcnow().isoformat()
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            """
            INSERT INTO scans(scan_id, created_at, status, request_json)
            VALUES (?, ?, ?, ?)
            """,
            (str(scan_id), created_at, "queued", json.dumps(request)),
        )
        await db.commit()
    return scan_id


async def mark_running(db_path: str, scan_id: UUID) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            """
            UPDATE scans
            SET status = ?, started_at = ?
            WHERE scan_id = ?
            """,
            ("running", _utcnow().isoformat(), str(scan_id)),
        )
        await db.commit()


async def set_result(db_path: str, scan_id: UUID, result: dict[str, Any]) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            """
            UPDATE scans
            SET status = ?, finished_at = ?, result_json = ?, error = NULL
            WHERE scan_id = ?
            """,
            ("completed", _utcnow().isoformat(), json.dumps(result), str(scan_id)),
        )
        await db.commit()


async def set_failed(db_path: str, scan_id: UUID, error: str) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            """
            UPDATE scans
            SET status = ?, finished_at = ?, error = ?
            WHERE scan_id = ?
            """,
            ("failed", _utcnow().isoformat(), error, str(scan_id)),
        )
        await db.commit()


async def get_scan(db_path: str, scan_id: UUID) -> dict[str, Any] | None:
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM scans WHERE scan_id = ?", (str(scan_id),))
        row = await cur.fetchone()
        if not row:
            return None
        return dict(row)


async def list_scans(db_path: str, limit: int = 50) -> list[dict[str, Any]]:
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM scans ORDER BY created_at DESC LIMIT ?",
            (int(limit),),
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]


async def list_scans_brief(db_path: str, limit: int = 50) -> list[dict[str, Any]]:
    """
    List scans without returning/parsing potentially large result_json.
    """
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """
            SELECT
              scan_id,
              created_at,
              status,
              started_at,
              finished_at,
              request_json,
              NULL AS result_json,
              error
            FROM scans
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (int(limit),),
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]


async def get_latest_scan(db_path: str) -> dict[str, Any] | None:
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM scans ORDER BY created_at DESC LIMIT 1")
        row = await cur.fetchone()
        if not row:
            return None
        return dict(row)


async def create_market_scan(db_path: str) -> UUID:
    scan_id = uuid4()
    created_at = _utcnow().isoformat()
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            """
            INSERT INTO market_scans(scan_id, created_at, status)
            VALUES (?, ?, ?)
            """,
            (str(scan_id), created_at, "queued"),
        )
        await db.commit()
    return scan_id


async def mark_market_running(db_path: str, scan_id: UUID) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            """
            UPDATE market_scans
            SET status = ?, started_at = ?
            WHERE scan_id = ?
            """,
            ("running", _utcnow().isoformat(), str(scan_id)),
        )
        await db.commit()


async def set_market_result(db_path: str, scan_id: UUID, result: dict[str, Any]) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            """
            UPDATE market_scans
            SET status = ?, finished_at = ?, result_json = ?, error = NULL
            WHERE scan_id = ?
            """,
            ("completed", _utcnow().isoformat(), json.dumps(result), str(scan_id)),
        )
        await db.commit()


async def set_market_failed(db_path: str, scan_id: UUID, error: str) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            """
            UPDATE market_scans
            SET status = ?, finished_at = ?, error = ?
            WHERE scan_id = ?
            """,
            ("failed", _utcnow().isoformat(), error, str(scan_id)),
        )
        await db.commit()


async def get_market_scan(db_path: str, scan_id: UUID) -> dict[str, Any] | None:
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM market_scans WHERE scan_id = ?", (str(scan_id),))
        row = await cur.fetchone()
        if not row:
            return None
        return dict(row)


async def get_latest_market_scan(db_path: str) -> dict[str, Any] | None:
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM market_scans ORDER BY created_at DESC LIMIT 1")
        row = await cur.fetchone()
        if not row:
            return None
        return dict(row)


async def list_market_scans(db_path: str, limit: int = 20) -> list[dict[str, Any]]:
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM market_scans ORDER BY created_at DESC LIMIT ?",
            (int(limit),),
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]

