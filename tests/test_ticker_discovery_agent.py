from __future__ import annotations

import httpx
import pytest

from invest_scan.agents.ticker_discovery_agent import TickerDiscoveryAgent
from invest_scan.settings import Settings


@pytest.mark.asyncio
async def test_ticker_discovery_intersects_with_base_universe():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "query2.finance.yahoo.com" and request.url.path.endswith(
            "/v1/finance/screener/predefined/saved"
        ):
            payload = {
                "finance": {
                    "result": [
                        {
                            "quotes": [
                                {"symbol": "AAPL"},
                                {"symbol": "TSLA"},
                                {"symbol": "BRK.B"},
                            ]
                        }
                    ],
                    "error": None,
                }
            }
            return httpx.Response(200, json=payload)
        return httpx.Response(404, text="not found")

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        agent = TickerDiscoveryAgent(
            http=http,
            settings=Settings(
                ticker_discovery_enabled=True,
                ticker_discovery_screener_ids_csv="most_actives",
                ticker_discovery_count_per_screener=50,
                ticker_discovery_max_tickers=10,
            ),
        )
        base = ["AAPL", "MSFT", "BRK-B"]
        res = await agent.discover(base_tickers=base, max_tickers=10)
        assert res["enabled"] is True
        assert res["tickers"] == ["AAPL", "BRK-B"]
    finally:
        await http.aclose()

