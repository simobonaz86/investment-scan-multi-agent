from __future__ import annotations

import aiosqlite


async def test_portfolio_config_and_plan_api(app, client):
    # Set portfolio config (1% sleeve default is fine; set total portfolio).
    r = await client.post(
        "/api/config/portfolio",
        json={
            "total_portfolio_usd": 100000.0,
            "sleeve_pct": 0.01,
            "max_positions": 4,
            "risk_per_trade_pct": 0.01,
            "max_position_pct": 0.35,
        },
    )
    assert r.status_code == 200
    assert r.json()["saved"]["total_portfolio_usd"] == 100000.0

    # Insert a single active recommendation and a WAITING intraday status.
    async with aiosqlite.connect(app.state.settings.db_path) as db:
        await db.execute(
            """
            INSERT INTO recommendations(
              rec_id, ticker, strategy, rating, score, mechanisms, reasons,
              entry_price, stop_loss, take_profit, shares, notional_usd,
              max_loss_usd, risk_reward_ratio, cash_after, status,
              source_scan_id, created_at, expires_at
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?)
            """,
            (
                "rec-1",
                "AAPL",
                "momentum",
                "Strong",
                15.0,
                "[]",
                '["uptrend"]',
                100.0,
                95.0,
                107.5,
                1,
                100.0,
                5.0,
                1.5,
                999999.0,
                "scan-1",
                "2026-01-01T00:00:00+00:00",
                "2099-01-02T00:00:00+00:00",
            ),
        )
        await db.execute(
            """
            INSERT INTO intraday_watchlist(
              ticker, rec_id, score, rating, setup_type, status, interval, updated_at
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "AAPL",
                "rec-1",
                15.0,
                "Strong",
                "pullback_reclaim",
                "WAITING",
                "15m",
                "2026-01-01T00:00:00+00:00",
            ),
        )
        await db.commit()

    plan = (await client.get("/api/portfolio/plan")).json()
    assert plan["ok"] is True
    assert plan["sleeve_value_usd"] == 1000.0  # 1% of 100k
    assert plan["lines"], "expected at least one plan line"
    line = plan["lines"][0]
    assert line["ticker"] == "AAPL"
    assert line["shares"] > 0
    assert line["notional_usd"] <= plan["max_notional_per_position_usd"] + 1e-6

