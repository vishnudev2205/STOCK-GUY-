"""Trading charges and capital gains tax for Indian equity delivery.

Tax rules change over time, so they are looked up by the date of the sale:
  - Sold before 1 Apr 2018: long-term gains were exempt. Short-term 15%.
  - 1 Apr 2018 to 22 Jul 2024: long-term 10% above Rs 1,00,000 a year. Short-term 15%.
  - From 23 Jul 2024: long-term 12.5% above Rs 1,25,000 a year. Short-term 20%.
"Long-term" means held more than 12 months. 4% cess is added. Surcharge is ignored.
"""
from __future__ import annotations

import pandas as pd

from .config import Config

LTCG_START = pd.Timestamp("2018-04-01")
GRANDFATHER = pd.Timestamp("2018-01-31")
BUDGET_2024 = pd.Timestamp("2024-07-23")


def charges(value: float, side: str, cfg: Config) -> float:
    """All statutory charges for one delivery trade of `value` rupees (slippage is in the price)."""
    base = value * (cfg.brokerage + cfg.exch_txn + cfg.sebi)
    total = base * (1 + cfg.gst) + value * cfg.stt
    total += value * cfg.stamp_buy if side == "BUY" else cfg.dp_charge
    return total


def fy_of(date) -> int:
    """Indian financial year, named by its starting year (Apr 2024-Mar 2025 -> 2024)."""
    d = pd.Timestamp(date)
    return d.year if d.month >= 4 else d.year - 1


def is_long_term(buy_date, sell_date) -> bool:
    return pd.Timestamp(sell_date) > pd.Timestamp(buy_date) + pd.DateOffset(months=12)


def tax_for_year(realized: list[dict], carry: dict, cfg: Config) -> tuple[float, dict, dict]:
    """Tax for one financial year.

    realized: [{buy_date, sell_date, gain}], all sold in the same FY.
    carry: {"st": loss, "lt": loss} brought forward (positive numbers).
    Returns (tax, new_carry, breakdown).
    """
    st = {"old": 0.0, "new": 0.0}
    lt = {"old": 0.0, "new": 0.0}
    for r in realized:
        sell = pd.Timestamp(r["sell_date"])
        era = "new" if sell >= BUDGET_2024 else "old"
        if is_long_term(r["buy_date"], sell):
            if sell >= LTCG_START:          # before that it was exempt, and losses did not count either
                lt[era] += r["gain"]
        else:
            st[era] += r["gain"]
    st_net, lt_net = st["old"] + st["new"], lt["old"] + lt["new"]
    st_rate = _blend(st, 0.15, 0.20)
    lt_rate = _blend(lt, 0.10, 0.125)
    exemption = 125_000.0 if lt["new"] != 0 or _any_after(realized) else 100_000.0

    carry = {"st": carry.get("st", 0.0), "lt": carry.get("lt", 0.0)}
    if st_net < 0:                          # short-term loss can reduce long-term gains
        used = min(-st_net, max(lt_net, 0.0))
        lt_net -= used
        carry["st"] += -st_net - used
        st_net = 0.0
    if lt_net < 0:                          # long-term loss can only wait for future long-term gains
        carry["lt"] += -lt_net
        lt_net = 0.0
    for bucket in ("st", "lt"):             # brought-forward losses
        if bucket == "st":
            used = min(carry["st"], st_net); st_net -= used; carry["st"] -= used
            used = min(carry["st"], lt_net); lt_net -= used; carry["st"] -= used
        else:
            used = min(carry["lt"], lt_net); lt_net -= used; carry["lt"] -= used
    taxable_lt = max(0.0, lt_net - exemption)
    tax = (st_net * st_rate + taxable_lt * lt_rate) * (1 + cfg.cess)
    return tax, carry, {"short_term_gain": st_net, "long_term_gain": lt_net,
                        "long_term_taxable": taxable_lt, "tax": tax}


def _blend(bucket, old_rate, new_rate):
    pos_old, pos_new = max(bucket["old"], 0.0), max(bucket["new"], 0.0)
    if pos_old + pos_new == 0:
        return new_rate
    return (pos_old * old_rate + pos_new * new_rate) / (pos_old + pos_new)


def _any_after(realized):
    return any(pd.Timestamp(r["sell_date"]) >= BUDGET_2024 for r in realized)


def taxable_gain(buy_price, sell_price, qty, buy_date, sell_date, fmv_31jan2018=None) -> float:
    """Gain for tax. Shares bought before 1 Feb 2018 get the 'grandfathering' relief."""
    cost = buy_price
    if (fmv_31jan2018 is not None and pd.Timestamp(buy_date) <= GRANDFATHER
            and pd.Timestamp(sell_date) >= LTCG_START):
        cost = max(buy_price, min(fmv_31jan2018, sell_price))
    return (sell_price - cost) * qty
