from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import aiosqlite

from invest_scan.agents.signals_agent import SignalsAgent


async def test_trade_execute_cash_validation_and_close_pnl(client):
    # Start with limited cash
    r_cash = await client.post("/portfolio/cash", json={"cash_usd": 50.0})
    assert r_cash.status_code == 200

    # Too expensive: 10 shares * $10 = $100 > $50
    r = await client.post(
        "/api/trade/execute",
        json={"ticker": "AAPL", "entry_price": 10.0, "shares": 10, "strategy": "manual"},
    )
    assert r.status_code == 400
    assert "insufficient_cash" in r.json()["detail"]

    # Execute within cash
    r = await client.post(
        "/api/trade/execute",
        json={
            "ticker": "AAPL",
            "entry_price": 10.0,
            "shares": 4,
            "stop_loss": 9.0,
            "take_profit": 12.0,
            "strategy": "manual",
        },
    )
    assert r.status_code == 200
    trade = r.json()
    assert trade["status"] == "open"
    assert trade["ticker"] == "AAPL"
    assert trade["cost_basis"] == 40.0

    # Cash reduced and position created
    p = (await client.get("/portfolio")).json()
    assert p["cash_usd"] == 10.0
    assert any(x["ticker"] == "AAPL" and x["quantity"] == 4 for x in p["positions"])

    # Close at profit: (12 - 10) * 4 = 8
    trade_id = trade["trade_id"]
    r = await client.post(
        f"/api/trade/close/{trade_id}",
        json={"exit_price": 12.0, "exit_reason": "manual"},
    )
    assert r.status_code == 200
    closed = r.json()
    assert closed["status"] == "closed"
    assert closed["realised_pnl"] == 8.0
    assert closed["exit_price"] == 12.0
    assert closed["holding_days"] >= 0

    # Cash restored: 10 + (12*4)=58
    p2 = (await client.get("/portfolio")).json()
    assert p2["cash_usd"] == 58.0
    assert not any(x["ticker"] == "AAPL" and float(x["quantity"]) > 0 for x in p2["positions"])


def test_bollinger_band_formula():
    closes = [float(i) for i in range(1, 21)]
    s = SignalsAgent().analyze(closes, market={})
    assert s["sma20"] == 10.5

    # Population stdev for 1..20 is sqrt((n^2-1)/12) = sqrt(33.25)
    # Upper/lower = mean +/- 2*sd
    assert abs(s["bollinger_upper"] - 22.032690) < 1e-3
    assert abs(s["bollinger_lower"] - (-1.032690)) < 1e-3
    # Position in bands: (20 - lower) / (upper-lower)
    assert abs(s["bollinger_position"] - 0.911765) < 1e-3


async def test_recommendation_expiry_logic(app, client):
    # Ensure a market scan runs and creates at least one recommendation.
    r_cash = await client.post("/portfolio/cash", json={"cash_usd": 10000})
    assert r_cash.status_code == 200

    r = await client.post("/marketscan/run")
    assert r.status_code == 200

    for _ in range(80):
        latest = await client.get("/marketscan/latest")
        if latest.status_code == 200 and latest.json()["status"] in ("completed", "failed"):
            break
        await asyncio.sleep(0.02)

    recs = (await client.get("/api/recommendations?status=active&limit=10")).json()[
        "recommendations"
    ]
    assert recs, "expected at least one active recommendation"
    rid = recs[0]["rec_id"]

    # Force it expired in DB, then run expiry job and confirm it disappears from active list.
    past = datetime(2000, 1, 1, tzinfo=UTC).isoformat()
    async with aiosqlite.connect(app.state.settings.db_path) as db:
        await db.execute(
            "UPDATE recommendations SET expires_at=? WHERE rec_id=?",
            (past, rid),
        )
        await db.commit()

    expired = await app.state.recommendation_service.expire_due()
    assert expired >= 1

    active2 = (await client.get("/api/recommendations?status=active&limit=50")).json()[
        "recommendations"
    ]
    assert rid not in {x["rec_id"] for x in active2}

    exp_list = (await client.get("/api/recommendations?status=expired&limit=50")).json()[
        "recommendations"
    ]
    assert rid in {x["rec_id"] for x in exp_list}

