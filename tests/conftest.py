from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Callable

import httpx
import pytest

from invest_scan.main import create_app
from invest_scan.settings import Settings


def _make_stooq_csv(days: int = 120) -> str:
    start = dt.date(2025, 1, 1)
    lines = ["Date,Open,High,Low,Close,Volume"]
    price = 100.0
    for i in range(days):
        day = start + dt.timedelta(days=i)
        # simple upward drift, skip weekends not needed for MVP math
        price = price * (1.0 + (0.001 if i % 5 else -0.0005))
        close = round(price, 4)
        lines.append(f"{day.isoformat()},1,1,1,{close},0")
    return "\n".join(lines) + "\n"


def _make_rss_xml() -> str:
    return """<?xml version="1.0" encoding="UTF-8" ?>
    <rss version="2.0">
      <channel>
        <title>Mock News</title>
        <item>
          <title>Example headline</title>
          <link>https://example.com/article</link>
          <pubDate>Mon, 01 Jan 2025 00:00:00 GMT</pubDate>
        </item>
      </channel>
    </rss>
    """


@pytest.fixture()
def app(tmp_path: Path):
    csv_body = _make_stooq_csv()
    rss_body = _make_rss_xml()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "stooq.com" and request.url.path == "/q/d/l/":
            return httpx.Response(200, text=csv_body)
        if request.url.host == "news.google.com":
            return httpx.Response(200, text=rss_body)
        return httpx.Response(404, text="not found")

    transport = httpx.MockTransport(handler)
    settings = Settings(db_path=str(tmp_path / "test.db"), cache_ttl_seconds=1)
    return create_app(settings_obj=settings, transport=transport)


@pytest.fixture()
async def client(app) -> httpx.AsyncClient:
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            yield c

