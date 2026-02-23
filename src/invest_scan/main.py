from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from starlette.middleware.gzip import GZipMiddleware

from invest_scan import db
from invest_scan.autoscan import autoscan_loop
from invest_scan.api import router
from invest_scan.settings import Settings, settings
from invest_scan.services.portfolio_service import PortfolioService
from invest_scan.services.ranking_service import RankingService
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
            limits=httpx.Limits(
                max_connections=max(10, settings_obj.max_concurrent_fetches * 4),
                max_keepalive_connections=max(10, settings_obj.max_concurrent_fetches * 2),
                keepalive_expiry=30.0,
            ),
        )
        app.state.settings = settings_obj
        app.state.http = http
        app.state.ranking_service = RankingService(settings=settings_obj, http=http)
        app.state.portfolio_service = PortfolioService(settings=settings_obj)
        app.state.scan_service = ScanService(
            settings=settings_obj, http=http, portfolio_service=app.state.portfolio_service
        )
        autoscan_task: asyncio.Task | None = None
        if settings_obj.autoscan_enabled:
            autoscan_task = asyncio.create_task(autoscan_loop(app))
            app.state.autoscan_task = autoscan_task
        try:
            yield
        finally:
            if autoscan_task is not None:
                autoscan_task.cancel()
                try:
                    await autoscan_task
                except asyncio.CancelledError:
                    pass
            await http.aclose()

    app = FastAPI(title="Investment Scan MVP", version="0.1.0", lifespan=lifespan)
    app.add_middleware(GZipMiddleware, minimum_size=1500)
    app.include_router(router)
    return app


app = create_app()

