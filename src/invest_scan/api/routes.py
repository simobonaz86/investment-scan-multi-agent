from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse

from invest_scan import db
from invest_scan.models import ScanCreateResponse, ScanListResponse, ScanRecord, ScanRequest, ScanStatusResponse
from invest_scan.services.scan_service import scan_record_from_row


router = APIRouter()
_UI_DIR = Path(__file__).resolve().parents[1] / "ui"


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
        <p>Open the <a href="/app">Dashboard</a> or use the API docs at <a href="/docs">/docs</a>.</p>
        <pre>
POST /scan {"tickers":["AAPL","MSFT"],"as_of":"auto"}
GET  /scan/{scan_id}
GET  /scans
        </pre>
      </body>
    </html>
    """


@router.get("/app", response_class=FileResponse)
async def dashboard() -> FileResponse:
    return FileResponse(_UI_DIR / "index.html")


@router.get("/health")
async def health() -> dict[str, Any]:
    return {"ok": True, "service": "invest-scan", "time": datetime.now(timezone.utc).isoformat()}


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
async def list_scans(request: Request, limit: int = 50, include_result: bool = False) -> ScanListResponse:
    db_path = request.app.state.settings.db_path
    if include_result:
        rows = await db.list_scans(db_path, limit=limit)
    else:
        rows = await db.list_scans_brief(db_path, limit=limit)
    return ScanListResponse(scans=[_row_to_scan_record(r) for r in rows])


@router.get("/rankings/sp500/weekly")
async def sp500_weekly_ranking(request: Request) -> dict[str, Any]:
    settings = request.app.state.settings
    if not settings.sp500_weekly_ranking_enabled:
        raise HTTPException(status_code=403, detail="sp500_weekly_ranking_disabled")
    svc = request.app.state.ranking_service
    return await svc.sp500_weekly(
        universe_path=settings.sp500_universe_path,
        max_tickers=settings.sp500_ranking_max_tickers,
    )


@router.get("/autoscan/status")
async def autoscan_status(request: Request) -> dict[str, Any]:
    s = request.app.state.settings
    return {
        "enabled": bool(s.autoscan_enabled),
        "interval_seconds": s.autoscan_interval_seconds,
        "tickers_csv": s.autoscan_tickers_csv,
        "only_market_hours": bool(s.autoscan_only_market_hours),
        "market_timezone": s.market_timezone,
        "market_open_hhmm": s.market_open_hhmm,
        "market_close_hhmm": s.market_close_hhmm,
    }


@router.get("/portfolio")
async def get_portfolio(request: Request) -> dict[str, Any]:
    p = await request.app.state.portfolio_service.get_portfolio()
    return {"account_id": p.account_id, "cash_usd": p.cash_usd, "positions": p.positions}


@router.post("/portfolio/cash")
async def set_cash(request: Request, body: dict[str, Any]) -> dict[str, Any]:
    cash = body.get("cash_usd")
    if cash is None:
        raise HTTPException(status_code=400, detail="missing_cash_usd")
    try:
        cash_f = float(cash)
    except Exception:
        raise HTTPException(status_code=400, detail="invalid_cash_usd")
    await request.app.state.portfolio_service.set_cash_usd(cash_f)
    return {"ok": True, "cash_usd": max(0.0, cash_f)}


@router.post("/portfolio/revolut/upload")
async def upload_revolut_csv(
    request: Request,
    file: UploadFile = File(...),
    mode: str = "auto",
) -> dict[str, Any]:
    content = await file.read()
    try:
        parsed = await request.app.state.portfolio_service.import_revolut_csv(content, mode=mode)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, **parsed}

