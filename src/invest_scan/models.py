from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field


class ScanRequest(BaseModel):
    tickers: list[str] = Field(..., min_length=1, max_length=30)
    as_of: str = Field(default="auto", description="MVP placeholder; use 'auto'.")


class ScanCreateResponse(BaseModel):
    scan_id: UUID
    status: Literal["queued", "running"]


class ScanRecord(BaseModel):
    scan_id: UUID
    created_at: datetime
    status: Literal["queued", "running", "completed", "failed"]
    started_at: datetime | None = None
    finished_at: datetime | None = None
    request: dict[str, Any]
    result: dict[str, Any] | None = None
    error: str | None = None


class ScanStatusResponse(BaseModel):
    scan: ScanRecord


class ScanListResponse(BaseModel):
    scans: list[ScanRecord]


class TradeExecuteRequest(BaseModel):
    ticker: str = Field(..., min_length=1, max_length=20)
    entry_price: float
    shares: float
    stop_loss: float | None = None
    take_profit: float | None = None
    strategy: str | None = None  # 'momentum', 'reversion', 'manual'
    reason: str | None = None
    source_scan_id: str | None = None


class TradeCloseRequest(BaseModel):
    exit_price: float
    exit_reason: str | None = None  # 'stop_loss', 'take_profit', 'manual', 'expired'


class TradeRecord(BaseModel):
    trade_id: str
    account_id: str
    ticker: str
    direction: Literal["long"] = "long"
    strategy: str | None = None
    status: Literal["open", "closed"]
    entry_price: float
    entry_date: datetime
    shares: float
    cost_basis: float
    stop_loss: float | None = None
    take_profit: float | None = None
    reason: str | None = None
    exit_price: float | None = None
    exit_date: datetime | None = None
    exit_reason: str | None = None
    realised_pnl: float | None = None
    holding_days: int | None = None
    source_scan_id: str | None = None
    created_at: datetime
    updated_at: datetime

