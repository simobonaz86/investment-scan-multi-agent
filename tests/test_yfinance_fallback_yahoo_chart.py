from __future__ import annotations

import httpx
import pytest

from invest_scan.agents.market_data_agent import MarketDataAgent


@pytest.mark.asyncio
async def test_yfinance_failure_falls_back_to_yahoo_chart(monkeypatch: pytest.MonkeyPatch):
    # Force yfinance to fail so we exercise the Yahoo chart fallback.
    import invest_scan.agents.market_data_agent as mda

    def boom(*args, **kwargs):
        raise RuntimeError("rate_limited")

    monkeypatch.setattr(mda.yf, "download", boom)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "query1.finance.yahoo.com" and request.url.path.startswith(
            "/v8/finance/chart/"
        ):
            payload = {
                "chart": {
                    "result": [
                        {
                            "timestamp": [1735689600, 1735776000],
                            "indicators": {
                                "quote": [
                                    {
                                        "open": [100.0, 101.0],
                                        "high": [101.0, 102.0],
                                        "low": [99.0, 100.0],
                                        "close": [100.5, 101.5],
                                        "volume": [1000, 1100],
                                    }
                                ]
                            },
                        }
                    ],
                    "error": None,
                }
            }
            return httpx.Response(200, json=payload)
        return httpx.Response(404, text="not found")

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        agent = MarketDataAgent(http)
        hist, src = await agent.fetch_history("AAPL")
        assert src in {"yahoo_chart", "finnhub", "none", "yfinance"}
        # In this test it should specifically fall back to yahoo_chart.
        assert src == "yahoo_chart"
        assert len(hist) == 2
        assert hist[-1].close == 101.5
    finally:
        await http.aclose()

