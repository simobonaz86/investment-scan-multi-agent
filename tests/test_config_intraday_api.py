from __future__ import annotations


async def test_intraday_config_get_and_set(client):
    r = await client.get("/api/config/intraday")
    assert r.status_code == 200
    data = r.json()
    assert "effective" in data

    r2 = await client.post(
        "/api/config/intraday",
        json={
            "enabled": True,
            "only_market_hours": False,
            "interval": "15m",
            "period": "5d",
            "watchlist_size": 15,
            "poll_seconds": 120,
        },
    )
    assert r2.status_code == 200
    saved = r2.json()["saved"]
    assert saved["enabled"] is True
    assert saved["only_market_hours"] is False
    assert saved["watchlist_size"] == 15
    assert saved["poll_seconds"] == 120

    r3 = await client.get("/api/config/intraday")
    assert r3.status_code == 200
    data3 = r3.json()
    assert data3["effective"]["watchlist_size"] == 15
    assert data3["effective"]["poll_seconds"] == 120

