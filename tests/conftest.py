from __future__ import annotations

import datetime as dt
from pathlib import Path

import httpx
import pytest

from invest_scan.main import create_app
from invest_scan.settings import Settings


def _make_yf_df(tickers: list[str], days: int = 120):
    import pandas as pd

    start = dt.date(2025, 1, 1)
    dates = pd.date_range(start=start, periods=days, freq="D")
    fields = ["Open", "High", "Low", "Close", "Volume"]
    cols = pd.MultiIndex.from_product([tickers, fields])

    data = []
    for i in range(days):
        row = []
        base = 100.0 * (1.0 + 0.001 * i)
        for _t in tickers:
            close = base
            open_ = close * 0.997
            high = close * 1.005
            low = close * 0.995
            vol = 1_000_000.0
            row.extend([open_, high, low, close, vol])
        data.append(row)

    return pd.DataFrame(data, index=dates, columns=cols)


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
def app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    rss_body = _make_rss_xml()
    universe_csv = "Symbol,Name,Sector\nAAPL,Apple,Tech\nMSFT,Microsoft,Tech\n"

    # Patch yfinance.download globally for tests (no real network).
    def fake_download(tickers: str, *args, **kwargs):
        tickers2 = [t for t in str(tickers).replace(",", " ").split() if t.strip()]
        tickers2 = [t.strip().upper() for t in tickers2]
        return _make_yf_df(tickers2 or ["AAPL"])

    import invest_scan.agents.market_data_agent as mda

    monkeypatch.setattr(mda.yf, "download", fake_download)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "news.google.com":
            return httpx.Response(200, text=rss_body)
        if request.url.host == "datahub.io" and request.url.path.endswith("/constituents.csv"):
            return httpx.Response(200, text=universe_csv)
        return httpx.Response(404, text="not found")

    transport = httpx.MockTransport(handler)
    settings = Settings(
        db_path=str(tmp_path / "test.db"),
        cache_ttl_seconds=1,
        marketscan_interval_seconds=999999,
    )
    return create_app(settings_obj=settings, transport=transport)


@pytest.fixture()
async def client(app) -> httpx.AsyncClient:
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            yield c

