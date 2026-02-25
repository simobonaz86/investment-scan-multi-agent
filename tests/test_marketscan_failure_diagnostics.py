from __future__ import annotations

import asyncio
from pathlib import Path

import httpx

from invest_scan.main import create_app
from invest_scan.settings import Settings


async def test_marketscan_includes_error_samples_when_stooq_returns_html(tmp_path: Path):
    universe_csv = "Symbol,Name,Sector\nAAPL,Apple,Tech\nMSFT,Microsoft,Tech\n"
    html = "<html><body>blocked</body></html>"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "datahub.io" and request.url.path.endswith("/constituents.csv"):
            return httpx.Response(200, text=universe_csv)
        if request.url.host == "stooq.com" and request.url.path == "/q/d/l/":
            return httpx.Response(200, headers={"content-type": "text/html"}, text=html)
        if request.url.host == "stooq.pl" and request.url.path == "/q/d/l/":
            return httpx.Response(200, headers={"content-type": "text/html"}, text=html)
        return httpx.Response(404, text="not found")

    app = create_app(
        settings_obj=Settings(
            db_path=str(tmp_path / "ms_fail.db"),
            cache_ttl_seconds=1,
            sp500_ranking_max_tickers=2,
            marketscan_interval_seconds=999999,
        ),
        transport=httpx.MockTransport(handler),
    )

    async with app.router.lifespan_context(app):
        client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
        await client.post("/portfolio/cash", json={"cash_usd": 10000})
        await client.post("/marketscan/run")

        for _ in range(80):
            latest = await client.get("/marketscan/latest")
            if latest.status_code == 200 and latest.json()["status"] in ("completed", "failed"):
                break
            await asyncio.sleep(0.02)

        latest = await client.get("/marketscan/latest")
        assert latest.status_code == 200
        res = latest.json()["result"]
        assert res["scored_size"] == 0
        assert res["failed_size"] >= 1
        assert res["errors_sample"], "expected sample errors when all tickers fail"
        await client.aclose()

