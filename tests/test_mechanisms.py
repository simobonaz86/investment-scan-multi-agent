from __future__ import annotations

from invest_scan.agents.signals_agent import SignalsAgent
from invest_scan.services.market_scan_service import _score_and_reasons


def test_trend_pullback_and_squeeze_mechanisms_flagged():
    # Build a gentle uptrend with low volatility and a small pullback on the last day.
    closes = [100.0 + (i * 0.10) for i in range(80)]
    base = closes[:-1]
    sig_base = SignalsAgent().analyze(base, market={})
    sma20 = float(sig_base["sma20"])
    sma50 = float(sig_base["sma50"])
    assert sma20 > sma50

    # Pull back to just under SMA20 while staying above SMA50.
    last = max(sma50 * 1.01, sma20 * 0.995)
    closes[-1] = float(last)

    signals = SignalsAgent().analyze(closes, market={"return_1w": 0.01, "return_1m": 0.03, "return_3m": 0.05})
    assert signals["bollinger_width_pct"] is not None

    scored = _score_and_reasons(market={"volatility_60d_ann": 0.2}, signals=signals)
    mechs = set(scored["mechanisms"])
    assert "pullback_in_uptrend" in mechs
    assert "volatility_squeeze" in mechs
    assert scored["rating"] in {"Very Strong", "Strong", "Light", "Not strong"}
