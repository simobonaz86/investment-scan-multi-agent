from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, Response

from invest_scan import db
from invest_scan.models import (
    ScanCreateResponse,
    ScanListResponse,
    ScanRecord,
    ScanRequest,
    ScanStatusResponse,
    TradeCloseRequest,
    TradeExecuteRequest,
)
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


@router.get("/marketscan/status")
async def marketscan_status(request: Request) -> dict[str, Any]:
    s = request.app.state.settings
    return {
        "enabled": bool(s.marketscan_enabled),
        "interval_seconds": s.marketscan_interval_seconds,
        "only_market_hours": bool(s.marketscan_only_market_hours),
        "top_n": s.marketscan_top_n,
        "min_score": s.marketscan_min_score,
        "universe_source": s.universe_source,
        "universe_refresh_seconds": s.universe_refresh_seconds,
    }


@router.post("/marketscan/run")
async def marketscan_run(request: Request) -> dict[str, Any]:
    scan_id = await db.create_market_scan(request.app.state.settings.db_path)
    asyncio.create_task(request.app.state.market_scan_service.run_and_persist(scan_id=scan_id))
    return {"scan_id": scan_id, "status": "queued"}


@router.get("/marketscan/latest")
async def marketscan_latest(request: Request) -> dict[str, Any]:
    row = await db.get_latest_market_scan(request.app.state.settings.db_path)
    if not row:
        raise HTTPException(status_code=404, detail="no_market_scan_found")
    result = json.loads(row["result_json"]) if row.get("result_json") else None
    return {
        "scan_id": row["scan_id"],
        "created_at": row["created_at"],
        "status": row["status"],
        "result": result,
        "error": row.get("error"),
    }


@router.get("/universe")
async def universe(request: Request) -> dict[str, Any]:
    return await request.app.state.universe_service.get_universe()



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


@router.post("/api/trade/execute")
async def trade_execute(request: Request, body: TradeExecuteRequest) -> dict[str, Any]:
    svc = request.app.state.trade_service
    try:
        trade = await svc.execute(**body.model_dump())
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return trade


@router.post("/api/trade/close/{trade_id}")
async def trade_close(trade_id: str, request: Request, body: TradeCloseRequest) -> dict[str, Any]:
    svc = request.app.state.trade_service
    try:
        trade = await svc.close(trade_id=trade_id, **body.model_dump())
    except KeyError:
        raise HTTPException(status_code=404, detail="trade_not_found")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return trade


@router.get("/api/trades")
async def list_trades(request: Request, status: str = "all", limit: int = 50) -> dict[str, Any]:
    svc = request.app.state.trade_service
    st = str(status or "all").lower()
    if st not in {"open", "closed", "all"}:
        raise HTTPException(status_code=400, detail="invalid_status")
    trades = await svc.list(status=st, limit=limit)
    return {"trades": trades}


@router.get("/api/trade/{trade_id}")
async def get_trade(trade_id: str, request: Request) -> dict[str, Any]:
    svc = request.app.state.trade_service
    trade = await svc.get(trade_id=trade_id)
    if not trade:
        raise HTTPException(status_code=404, detail="trade_not_found")
    return trade


@router.get("/api/journal/summary")
async def journal_summary(request: Request) -> dict[str, Any]:
    return await request.app.state.journal_service.summary()


@router.get("/api/journal/export")
async def journal_export(request: Request) -> Response:
    export = await request.app.state.journal_service.export_closed_csv()
    return Response(
        content=export.data,
        media_type=export.content_type,
        headers={"content-disposition": f'attachment; filename="{export.filename}"'},
    )


@router.get("/api/journal/daily")
async def journal_daily(request: Request) -> dict[str, Any]:
    return await request.app.state.journal_service.daily()

