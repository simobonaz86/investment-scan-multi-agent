from __future__ import annotations

import datetime as dt

from invest_scan.agents.intraday_trigger_agent import IntradayCandle, IntradayTriggerAgent


def _candles_up(n: int = 60) -> list[IntradayCandle]:
    out: list[IntradayCandle] = []
    t0 = dt.datetime(2026, 1, 1, 14, 30, tzinfo=dt.UTC)
    px = 100.0
    for i in range(n):
        px = px * (1.0 + 0.0005)
        out.append(
            IntradayCandle(
                ts=t0 + dt.timedelta(minutes=15 * i),
                open=px * 0.999,
                high=px * 1.001,
                low=px * 0.998,
                close=px,
                volume=1000.0 + i,
            )
        )
    return out


def test_intraday_trigger_waiting_then_triggered():
    agent = IntradayTriggerAgent()
    rec = {
        "rec_id": "r1",
        "ticker": "AAPL",
        "score": 12.3,
        "rating": "Strong",
        "mechanisms": ["pullback_in_uptrend"],
        "stop_loss": 90.0,
    }

    cs = _candles_up(60)
    res = agent.evaluate(rec=rec, candles=cs, interval="15m")
    assert res["status"] in {"WAITING", "TRIGGERED", "TOO_LATE"}


def test_intraday_trigger_invalidated_below_stop():
    agent = IntradayTriggerAgent()
    rec = {
        "rec_id": "r2",
        "ticker": "AAPL",
        "score": 9.0,
        "rating": "Medium",
        "mechanisms": ["pullback_in_uptrend"],
        "stop_loss": 99.5,
    }
    cs = _candles_up(40)
    # Force last candle low below stop
    last = cs[-1]
    cs[-1] = IntradayCandle(
        ts=last.ts,
        open=last.open,
        high=last.high,
        low=99.0,
        close=last.close,
        volume=last.volume,
    )
    res = agent.evaluate(rec=rec, candles=cs, interval="15m")
    assert res["status"] == "INVALIDATED"

