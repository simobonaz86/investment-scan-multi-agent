from __future__ import annotations


def normalize_yahoo_symbol(sym: str) -> str:
    """
    Normalize a symbol to Yahoo Finance / yfinance conventions.

    - Class shares: BRK.B -> BRK-B
    - Exchange suffix tickers (e.g. VUAA.L) are kept as-is.
    """
    s = str(sym or "").strip().upper()
    if not s:
        return ""
    if s.count(".") == 1:
        left, right = s.split(".", 1)
        # Only normalize the common US class-share pattern (BRK.B, BF.B).
        if left and right and right in {"A", "B", "C"} and left.replace("-", "").isalnum():
            return f"{left}-{right}"
    return s

