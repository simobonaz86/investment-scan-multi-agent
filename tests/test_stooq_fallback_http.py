from __future__ import annotations

import datetime as dt

import httpx
import pytest

from invest_scan.agents.market_data_agent import MarketDataAgent


@pytest.mark.asyncio
async def test_stooq_https_falls_back_to_http():
    csv_body = "Date,Open,High,Low,Close,Volume\n2025-01-01,100,101,99,100,1000\n2025-01-02,100,102,99,101,1100\n"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host in {"stooq.com", "stooq.pl"} and request.url.path == "/q/d/l/":
            if request.url.scheme == "https":
                raise httpx.ConnectError("blocked", request=request)
            return httpx.Response(200, text=csv_body)
        return httpx.Response(404, text="not found")

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        agent = MarketDataAgent(http)
        hist = await agent.fetch_history("AAPL")
        assert len(hist) == 2
        assert hist[-1].day == dt.date(2025, 1, 2)
    finally:
        await http.aclose()

