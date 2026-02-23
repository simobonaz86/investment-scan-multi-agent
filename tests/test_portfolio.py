from __future__ import annotations


async def test_portfolio_cash_and_upload_positions(client):
    r = await client.post("/portfolio/cash", json={"cash_usd": 1234.5})
    assert r.status_code == 200

    r = await client.get("/portfolio")
    assert r.status_code == 200
    body = r.json()
    assert body["cash_usd"] == 1234.5

    csv_content = "Ticker,Quantity,Average Price\nAAPL,2,150\nMSFT,1,300\n"
    files = {"file": ("revolut.csv", csv_content, "text/csv")}
    r = await client.post("/portfolio/revolut/upload?mode=positions", files=files)
    assert r.status_code == 200
    assert r.json()["mode"] == "positions"

    r = await client.get("/portfolio")
    positions = r.json()["positions"]
    assert any(p["ticker"] == "AAPL" and p["quantity"] == 2 for p in positions)

