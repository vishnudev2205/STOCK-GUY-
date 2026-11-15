"""Bot 5: Risk Manager. Decides how much, never whether a business is good.

Rules: at most 10% in one stock, at most 25% in one sector, 15-20 stocks.
If fewer than 15 companies pass every earlier bot, the rest stays in cash.
The bot will not lower the bar to fill slots.

It also reads your real Zerodha holdings (data/holdings.json, read-only) and
tells you where the paper portfolio overlaps with what you already own.
It only ever writes to the paper ledger.
"""
from __future__ import annotations

import json
from pathlib import Path

from ..config import Config


def load_real_holdings(cfg: Config) -> dict:
    p = Path(cfg.data_dir) / "holdings.json"
    if not p.exists():
        return {"holdings": [], "synced_at": None}
    try:
        return json.loads(p.read_text())
    except Exception:  # noqa: BLE001
        return {"holdings": [], "synced_at": None}


def real_weights(real: dict) -> dict:
    rows = real.get("holdings", [])
    vals = {h["symbol"]: float(h.get("quantity", 0)) * float(h.get("last_price") or h.get("average_price") or 0) for h in rows}
    total = sum(vals.values())
    return {s: v / total for s, v in vals.items()} if total > 0 else {}


def sector_weights(ledger, prices) -> dict:
    eq = ledger.equity(prices)
    out = {}
    for s, pos in ledger.positions.items():
        out[pos["sector"]] = out.get(pos["sector"], 0.0) + ledger.qty(s) * prices.get(s, 0) / eq
    return out


def trims(ledger, prices: dict, cfg: Config) -> list[dict]:
    """A winner that has grown past 12% of the portfolio is cut back to 10%."""
    eq = ledger.equity(prices)
    out = []
    for s in list(ledger.positions):
        p = prices.get(s)
        if not p:
            continue
        w = ledger.qty(s) * p / eq
        if w > cfg.max_stock_w + cfg.trim_tolerance:
            qty = int((w - cfg.max_stock_w) * eq / p)
            if qty > 0:
                out.append({"symbol": s, "qty": qty, "reason": f"Grew to {w:.0%} of the portfolio; cut back to {cfg.max_stock_w:.0%}"})
    return out


def cash_calls(ledger, prices: dict) -> list[dict]:
    """If tax or charges pushed cash below zero, sell a little of the biggest holdings."""
    need = -ledger.cash
    out = []
    if need <= 0:
        return out
    for s in sorted(ledger.positions, key=lambda x: -ledger.qty(x) * prices.get(x, 0)):
        p = prices.get(s)
        if not p or need <= 0:
            continue
        qty = min(ledger.qty(s), int(need * 1.02 / p) + 1)
        out.append({"symbol": s, "qty": qty, "reason": "Sold a little to pay tax"})
        need -= qty * p
    return out


def plan_buys(ledger, candidates: list[dict], prices: dict, cfg: Config, real: dict | None = None) -> tuple[list, list]:
    """candidates: assessments that passed bots 1-4, best first. Returns (orders, notes)."""
    real_w = real_weights(real or {})
    eq = ledger.equity(prices)
    sect = sector_weights(ledger, prices)
    fresh = [c for c in candidates if c["symbol"] not in ledger.positions and prices.get(c["symbol"])]
    n_target = max(cfg.min_stocks, min(cfg.max_stocks, len(ledger.positions) + len(fresh)))
    weight = min(cfg.max_stock_w, 1.0 / n_target)
    spendable = ledger.cash - cfg.cash_buffer * eq
    orders, notes = [], []
    count = len(ledger.positions)
    for c in fresh:
        if count >= cfg.max_stocks:
            notes.append(f"{c['symbol']}: passed every check but the portfolio is full ({cfg.max_stocks} stocks).")
            continue
        s, sec, p = c["symbol"], c["sector"], prices[c["symbol"]]
        if s in real_w:
            msg = f"{s}: you already own this in Zerodha ({real_w[s]:.0%} of your real portfolio)."
            if cfg.skip_if_held_real:
                notes.append(msg + " Skipped.")
                continue
            notes.append(msg)
        if sect.get(sec, 0.0) + weight > cfg.max_sector_w + 1e-9:
            notes.append(f"{s}: skipped, {sec} would go above {cfg.max_sector_w:.0%} of the portfolio.")
            continue
        qty = int(weight * eq / (p * (1 + cfg.slippage)))
        cost = qty * p * 1.003
        if qty <= 0 or cost > spendable:
            notes.append(f"{s}: passed every check but there is not enough paper cash left.")
            continue
        orders.append({"symbol": s, "qty": qty, "weight": weight})
        spendable -= cost
        sect[sec] = sect.get(sec, 0.0) + weight
        count += 1
    if count < cfg.min_stocks:
        notes.append(f"Only {count} stocks pass all checks (target is {cfg.min_stocks}-{cfg.max_stocks}). "
                     "The rest stays in cash rather than buying weaker companies.")
    return orders, notes


def overlap_report(ledger, real: dict) -> dict:
    real_w = real_weights(real or {})
    both = sorted(set(real_w) & set(ledger.positions))
    return {"synced_at": (real or {}).get("synced_at"), "n_real": len(real_w),
            "overlap": [{"symbol": s, "real_weight": real_w[s]} for s in both]}
