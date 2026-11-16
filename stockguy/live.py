"""Live prices for paper trades, so a paper buy is booked at the real price at the real time.

Source: Yahoo Finance 1-minute bars (free). NSE quotes on Yahoo can lag a few minutes.
Outside market hours you get the last trade of the day, stamped with its real time.
Read-only: this module fetches prices and nothing else.
"""
from __future__ import annotations

import pandas as pd

IST = "Asia/Kolkata"


def quotes(symbols, log=print) -> dict[str, tuple[float, pd.Timestamp]]:
    """{symbol: (last price, time of that price in IST)}. Symbols with no quote are left out."""
    import yfinance as yf
    out = {}
    for s in sorted(set(symbols)):
        try:
            h = yf.Ticker(f"{s}.NS").history(period="5d", interval="1m", auto_adjust=False)
            h = h[h["Close"] > 0]
            if h.empty:
                continue
            ts = pd.Timestamp(h.index[-1])
            ts = ts.tz_localize("UTC") if ts.tzinfo is None else ts
            out[s] = (float(h["Close"].iloc[-1]), ts.tz_convert(IST).tz_localize(None))
        except Exception as e:  # noqa: BLE001
            log(f"  ! no live quote for {s} ({type(e).__name__}); using the last close")
    return out


def market_open(now=None) -> bool:
    now = pd.Timestamp.now(tz=IST) if now is None else pd.Timestamp(now)
    t = now.hour * 60 + now.minute
    return now.weekday() < 5 and 9 * 60 + 15 <= t <= 15 * 60 + 30
