"""The trade log: every paper buy and sell, with the exact time, price and reason.

One entry per purchase. If it has been sold, the sale is on the same entry,
with its own time, price, reason and the profit after charges.
Writes data/trades.md and prints the same text.
"""
from __future__ import annotations

import pandas as pd


def _when(ts) -> str:
    if not ts:
        return "time not recorded"
    t = pd.Timestamp(ts)
    return t.strftime("%a %d %b %Y, %H:%M IST")


def rows(ledger, prices: dict | None = None) -> list[dict]:
    """Newest first. Each row: symbol, name, qty, buy_time, buy_price, buy_reason,
    and either sell_* fields or now_price for a stock still held."""
    prices = prices or {}
    out = []
    for c in ledger.closed:
        out.append({"symbol": c["symbol"], "name": c.get("name", c["symbol"]), "qty": c["qty"],
                    "buy_time": c.get("buy_time") or c["buy_date"], "buy_price": c["buy_price"],
                    "buy_reason": c.get("buy_reason", ""), "sell_time": c.get("sell_time") or c["sell_date"],
                    "sell_price": c["sell_price"], "sell_reason": c.get("reason", ""), "pnl": c["pnl"],
                    "ret": c["sell_price"] / c["buy_price"] - 1, "open": False})
    for s, pos in ledger.positions.items():
        for lot in pos["lots"]:
            now = prices.get(s)
            out.append({"symbol": s, "name": pos.get("name", s), "qty": lot["qty"],
                        "buy_time": lot.get("time") or lot["date"], "buy_price": lot["price"],
                        "buy_reason": lot.get("reason") or pos.get("thesis", {}).get("summary", ""),
                        "now_price": now, "pnl": (now - lot["price"]) * lot["qty"] if now else None,
                        "ret": now / lot["price"] - 1 if now else None, "open": True})
    out.sort(key=lambda r: str(r.get("sell_time") or r["buy_time"]), reverse=True)
    return out


def text(entries: list[dict]) -> str:
    if not entries:
        return "No paper trades yet. Run: python run.py buy"
    lines = []
    for r in entries:
        lines.append(f"BUY  {r['qty']} x {r['symbol']} ({r['name']})")
        lines.append(f"     {_when(r['buy_time'])} at Rs {r['buy_price']:,.2f}")
        lines.append(f"     Why: {r['buy_reason'] or 'no reason recorded'}")
        if r["open"]:
            if r.get("now_price"):
                lines.append(f"HOLD still held. Now Rs {r['now_price']:,.2f} "
                             f"({r['ret']:+.1%}, {r['pnl']:+,.0f} before selling charges and tax)")
            else:
                lines.append("HOLD still held. No current price.")
        else:
            lines.append(f"SELL {_when(r['sell_time'])} at Rs {r['sell_price']:,.2f}")
            lines.append(f"     Why: {r['sell_reason'] or 'no reason recorded'}")
            lines.append(f"     Result: {r['pnl']:+,.0f} ({r['ret']:+.1%}) after charges, before tax")
        lines.append("")
    return "\n".join(lines)


def write(ledger, path, prices: dict | None = None) -> str:
    entries = rows(ledger, prices)
    body = "# Trade log (paper money)\n\nNewest first. Prices include 0.1% slippage.\n\n```\n" + text(entries) + "\n```\n"
    path.write_text(body)
    return text(entries)
