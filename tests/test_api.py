from __future__ import annotations

import asyncio
from uuid import UUID


async def test_health(client):
    r = await client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True


async def test_scan_flow(client):
    r = await client.post("/scan", json={"tickers": ["AAPL"], "as_of": "auto"})
    assert r.status_code == 200
    scan_id = UUID(r.json()["scan_id"])

    # Poll briefly for background task completion
    for _ in range(50):
        s = await client.get(f"/scan/{scan_id}")
        assert s.status_code == 200
        status = s.json()["scan"]["status"]
        if status in ("completed", "failed"):
            break
        await asyncio.sleep(0.02)

    s = await client.get(f"/scan/{scan_id}")
    scan = s.json()["scan"]
    assert scan["status"] == "completed"
    assert scan["result"]["tickers"] == ["AAPL"]
    assert len(scan["result"]["reports"]) == 1
    assert scan["result"]["reports"][0]["ticker"] == "AAPL"

