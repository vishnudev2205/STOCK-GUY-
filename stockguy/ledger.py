"""The local simulated ledger. PAPER MONEY ONLY.

This file is plain bookkeeping in a JSON file on your disk. It imports no
broker library and makes no network calls. A "buy" here is a line in a file.
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from . import costs
from .config import Config

MODE = "PAPER"


def _d(x) -> str:
    return pd.Timestamp(x).strftime("%Y-%m-%d")


class Ledger:
    def __init__(self, cfg: Config, path: Path | None = None):
        self.cfg, self.path = cfg, path
        self.cash = cfg.capital
        self.start_capital = cfg.capital
        self.positions: dict[str, dict] = {}
        self.trades: list[dict] = []
        self.closed: list[dict] = []        # one row per fully or partly closed lot
        self.realized: list[dict] = []      # for tax
        self.tax_paid: list[dict] = []
        self.carry = {"st": 0.0, "lt": 0.0}
        self.tax_done_through: int | None = None
        self.charges_total = 0.0
        if path and path.exists():
            self.__dict__.update(json.loads(path.read_text()))
            self.cfg, self.path = cfg, path

    # ---- trades -----------------------------------------------------------
    def buy(self, symbol, qty: int, price: float, date, name="", sector="Unknown", thesis=None) -> dict | None:
        qty = int(qty)
        if qty <= 0:
            return None
        px = price * (1 + self.cfg.slippage)
        value = qty * px
        fee = costs.charges(value, "BUY", self.cfg)
        if value + fee > self.cash:
            return None
        self.cash -= value + fee
        self.charges_total += fee
        pos = self.positions.setdefault(symbol, {"name": name, "sector": sector, "lots": [],
                                                 "thesis": thesis or {}, "strikes": 0})
        pos["lots"].append({"date": _d(date), "qty": qty, "price": px})
        t = {"date": _d(date), "symbol": symbol, "side": "BUY", "qty": qty, "price": round(px, 2),
             "value": round(value, 2), "charges": round(fee, 2), "mode": MODE}
        self.trades.append(t)
        return t

    def sell(self, symbol, qty: int | None, price: float, date, reason="", fmv_2018=None) -> dict | None:
        pos = self.positions.get(symbol)
        if not pos:
            return None
        held = sum(l["qty"] for l in pos["lots"])
        qty = held if qty is None else min(int(qty), held)
        if qty <= 0:
            return None
        px = price * (1 - self.cfg.slippage)
        value = qty * px
        fee = costs.charges(value, "SELL", self.cfg) if value > 0 else 0.0
        self.cash += value - fee
        self.charges_total += fee
        left = qty
        while left > 0:                      # first in, first out
            lot = pos["lots"][0]
            take = min(left, lot["qty"])
            gain = costs.taxable_gain(lot["price"], px, take, lot["date"], date, fmv_2018)
            self.realized.append({"buy_date": lot["date"], "sell_date": _d(date), "gain": gain})
            self.closed.append({"symbol": symbol, "name": pos["name"], "buy_date": lot["date"],
                                "sell_date": _d(date), "qty": take, "buy_price": lot["price"],
                                "sell_price": px, "pnl": (px - lot["price"]) * take - fee * take / qty,
                                "reason": reason})
            lot["qty"] -= take
            left -= take
            if lot["qty"] == 0:
                pos["lots"].pop(0)
        if not pos["lots"]:
            del self.positions[symbol]
        t = {"date": _d(date), "symbol": symbol, "side": "SELL", "qty": qty, "price": round(px, 2),
             "value": round(value, 2), "charges": round(fee, 2), "reason": reason, "mode": MODE}
        self.trades.append(t)
        return t

    # ---- tax --------------------------------------------------------------
    def settle_tax(self, asof) -> list[dict]:
        """Pay capital gains tax for every financial year that has ended before `asof`."""
        last_done_fy = costs.fy_of(asof) - 1
        paid = []
        years = sorted({costs.fy_of(r["sell_date"]) for r in self.realized})
        for fy in years:
            if fy > last_done_fy or (self.tax_done_through is not None and fy <= self.tax_done_through):
                continue
            rows = [r for r in self.realized if costs.fy_of(r["sell_date"]) == fy]
            tax, self.carry, detail = costs.tax_for_year(rows, self.carry, self.cfg)
            self.cash -= tax
            rec = {"fy": f"{fy}-{str(fy + 1)[2:]}", "paid_on": _d(asof), **{k: round(v, 2) for k, v in detail.items()}}
            self.tax_paid.append(rec)
            paid.append(rec)
        self.tax_done_through = max(self.tax_done_through if self.tax_done_through is not None else -1,
                                    last_done_fy)
        return paid

    def exit_tax_estimate(self, prices: dict, asof) -> float:
        """Tax and charges you would owe if you sold everything today (for honest comparisons)."""
        rows = [r for r in self.realized
                if self.tax_done_through is None or costs.fy_of(r["sell_date"]) > self.tax_done_through]
        fees = 0.0
        for sym, pos in self.positions.items():
            p = prices.get(sym)
            if p is None:
                continue
            px = p * (1 - self.cfg.slippage)
            for lot in pos["lots"]:
                rows.append({"buy_date": lot["date"], "sell_date": _d(asof), "gain": (px - lot["price"]) * lot["qty"]})
            fees += costs.charges(self.qty(sym) * px, "SELL", self.cfg) + self.qty(sym) * p * self.cfg.slippage
        tax, _, _ = costs.tax_for_year(rows, dict(self.carry), self.cfg)
        return tax + fees

    # ---- reading ----------------------------------------------------------
    def qty(self, symbol) -> int:
        return sum(l["qty"] for l in self.positions.get(symbol, {"lots": []})["lots"])

    def avg_price(self, symbol) -> float:
        lots = self.positions[symbol]["lots"]
        return sum(l["qty"] * l["price"] for l in lots) / max(1, sum(l["qty"] for l in lots))

    def invested_value(self, prices: dict) -> float:
        return sum(self.qty(s) * prices[s] for s in self.positions if prices.get(s) is not None)

    def equity(self, prices: dict) -> float:
        return self.cash + self.invested_value(prices)

    def quantities(self) -> dict:
        return {s: self.qty(s) for s in self.positions}

    def save(self):
        if not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        d = {k: v for k, v in self.__dict__.items() if k not in ("cfg", "path")}
        d["mode"] = MODE
        self.path.write_text(json.dumps(d, indent=1, default=str))
