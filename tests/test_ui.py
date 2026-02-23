from __future__ import annotations


async def test_dashboard_served(client):
    r = await client.get("/app")
    assert r.status_code == 200
    assert "Investment Scan" in r.text

    r = await client.get("/static/app.js")
    assert r.status_code == 200
