from __future__ import annotations

import httpx
import pytest

from invest_scan.services.universe_service import UniverseService
from invest_scan.settings import Settings


@pytest.mark.asyncio
async def test_universe_yahoo_screener_normalizes_symbols():
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
                                {"symbol": "BRK.B"},  # should become BRK-B
                                {"symbol": "VUAA.L"},  # should stay VUAA.L
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
        svc = UniverseService(
            settings=Settings(
                universe_source="yahoo_screener",
                universe_yahoo_screener_id="most_actives",
                universe_yahoo_screener_count=250,
                universe_max_tickers=10,
            ),
            http=http,
        )
        uni = await svc.get_universe()
        assert uni["source"] == "yahoo_screener"
        assert uni["tickers"] == ["AAPL", "BRK-B", "VUAA.L"]
    finally:
        await http.aclose()

