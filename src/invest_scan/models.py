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

