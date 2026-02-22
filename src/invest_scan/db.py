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

