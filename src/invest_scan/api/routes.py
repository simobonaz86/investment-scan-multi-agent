from __future__ import annotations

import asyncio
import json
from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from invest_scan import db
from invest_scan.models import ScanCreateResponse, ScanListResponse, ScanRecord, ScanRequest, ScanStatusResponse
from invest_scan.services.scan_service import scan_record_from_row


router = APIRouter()


def _parse_dt(x: str | None) -> datetime | None:
    if not x:
        return None
    return datetime.fromisoformat(x)


def _row_to_scan_record(row: dict[str, Any]) -> ScanRecord:
    raw = scan_record_from_row(row)
    raw["created_at"] = _parse_dt(row["created_at"])
    raw["started_at"] = _parse_dt(row.get("started_at"))
    raw["finished_at"] = _parse_dt(row.get("finished_at"))
    raw["scan_id"] = UUID(row["scan_id"])
    return ScanRecord(**raw)


@router.get("/", response_class=HTMLResponse)
async def index() -> str:
    return """
    <html>
      <head><title>Investment Scan MVP</title></head>
      <body>
        <h2>Investment Scan Multi-Agent (MVP)</h2>
        <p>Use the API docs at <a href="/docs">/docs</a>.</p>
        <pre>
POST /scan {"tickers":["AAPL","MSFT"],"as_of":"auto"}
GET  /scan/{scan_id}
GET  /scans
        </pre>
      </body>
    </html>
    """


@router.get("/health")
async def health() -> dict[str, Any]:
    return {"ok": True, "service": "invest-scan", "time": datetime.utcnow().isoformat()}


@router.post("/scan", response_model=ScanCreateResponse)
async def create_scan(req: ScanRequest, request: Request) -> ScanCreateResponse:
    payload = req.model_dump()
    db_path = request.app.state.settings.db_path
    scan_id = await db.create_scan(db_path, payload)

    svc = request.app.state.scan_service
    asyncio.create_task(svc.run_and_persist(scan_id=scan_id, request=payload))
    return ScanCreateResponse(scan_id=scan_id, status="queued")


@router.get("/scan/{scan_id}", response_model=ScanStatusResponse)
async def get_scan(scan_id: UUID, request: Request) -> ScanStatusResponse:
    db_path = request.app.state.settings.db_path
    row = await db.get_scan(db_path, scan_id)
    if not row:
        raise HTTPException(status_code=404, detail="scan_not_found")
    return ScanStatusResponse(scan=_row_to_scan_record(row))


@router.get("/scans", response_model=ScanListResponse)
async def list_scans(request: Request, limit: int = 50) -> ScanListResponse:
    db_path = request.app.state.settings.db_path
    rows = await db.list_scans(db_path, limit=limit)
    return ScanListResponse(scans=[_row_to_scan_record(r) for r in rows])

