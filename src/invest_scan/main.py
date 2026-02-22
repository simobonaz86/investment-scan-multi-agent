from __future__ import annotations

from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI

from invest_scan import db
from invest_scan.api import router
from invest_scan.settings import Settings, settings
from invest_scan.services.scan_service import ScanService


def create_app(
    *,
    settings_obj: Settings = settings,
    transport: httpx.AsyncBaseTransport | None = None,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await db.init_db(settings_obj.db_path)
        http = httpx.AsyncClient(
            timeout=httpx.Timeout(settings_obj.http_timeout_seconds),
            headers={"user-agent": "invest-scan-mvp/0.1"},
            follow_redirects=True,
            transport=transport,
        )
        app.state.settings = settings_obj
        app.state.http = http
        app.state.scan_service = ScanService(settings=settings_obj, http=http)
        try:
            yield
        finally:
            await http.aclose()

    app = FastAPI(title="Investment Scan MVP", version="0.1.0", lifespan=lifespan)
    app.include_router(router)
    return app


app = create_app()

