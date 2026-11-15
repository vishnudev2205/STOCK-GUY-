"""Bot 2: Business Analyst. Is this a good business, judged on up to 10 years of numbers?

Checks: sales and profit growth, ROCE above 15%, low debt, cash flow backing
the profits, promoter holding and pledging.

Honest limits:
  - Yahoo's free data usually has only 4-5 years of annual statements. The bot
    uses whatever is published as of the date (minimum 3 years) and says how many.
  - Promoter holding and pledge history are not available free for past dates,
    so in the backtest those two checks are reported as "unknown", not passed.
  - Banks and NBFCs are judged on ROE; ROCE, debt and cash-flow tests do not fit them.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..config import Config


def cagr(first, last, years):
    if years <= 0 or pd.isna(first) or pd.isna(last) or first <= 0 or last <= 0:
        return None
    return float((last / first) ** (1 / years) - 1)


def _span_years(a):
    return (a.index[-1] - a.index[0]).days / 365.25


def run(view, cfg: Config) -> dict:
    a = view.annual.dropna(subset=["revenue", "net_income"])
    n = len(a)
    res = {"years": n, "passed": False, "score": 0, "metrics": {}, "checks": [], "notes": []}
    if n < cfg.min_years:
        res["notes"].append(f"Only {n} year(s) of published results on this date; need {cfg.min_years}.")
        return res
    if n < cfg.ideal_years:
        res["notes"].append(f"Short history: {n} years (ideal is 5-10).")
    yrs = _span_years(a)
    m = res["metrics"]
    m["rev_cagr"] = cagr(a["revenue"].iloc[0], a["revenue"].iloc[-1], yrs)
    m["pat_cagr"] = cagr(a["net_income"].iloc[0], a["net_income"].iloc[-1], yrs)
    m["loss_years"] = int((a["net_income"] <= 0).sum())
    growth_steps = a["net_income"].diff().dropna()
    m["profit_up_share"] = float((growth_steps > 0).mean()) if len(growth_steps) else None
    m["latest_pat"] = float(a["net_income"].iloc[-1])
    m["latest_revenue"] = float(a["revenue"].iloc[-1])
    margin = (a["net_income"] / a["revenue"]).replace([np.inf, -np.inf], np.nan).dropna()
    m["margin"] = float(margin.iloc[-1]) if len(margin) else None
    m["margin_wobble"] = float(margin.std() / abs(margin.mean())) if len(margin) >= 3 and margin.mean() else None

    roe = (a["net_income"] / a["equity"]).where(a["equity"] > 0).dropna()
    m["roe"] = float(roe.tail(3).mean()) if len(roe) else None
    cap = (a["total_assets"] - a["current_liabilities"])
    roce = (a["ebit"] / cap).where(cap > 0).dropna()
    m["roce"] = float(roce.tail(3).mean()) if len(roce) else None
    m["roce_latest"] = float(roce.iloc[-1]) if len(roce) else None
    last = a.iloc[-1]
    m["debt_equity"] = (float(last["total_debt"] / last["equity"])
                        if pd.notna(last["total_debt"]) and pd.notna(last["equity"]) and last["equity"] > 0 else None)
    cf = a.dropna(subset=["cfo"])
    pat_sum = cf["net_income"].sum()
    m["cfo_to_pat"] = float(cf["cfo"].sum() / pat_sum) if len(cf) and pat_sum > 0 else None
    fcf = (cf["cfo"] + cf["capex"].fillna(0)) if len(cf) else pd.Series(dtype=float)   # capex is negative in Yahoo
    m["fcf_positive_share"] = float((fcf > 0).mean()) if len(fcf) else None
    assets = a["total_assets"].dropna()
    m["asset_cagr"] = cagr(assets.iloc[0], assets.iloc[-1], (assets.index[-1] - assets.index[0]).days / 365.25) if len(assets) >= 2 else None
    m["promoter_pct"], m["pledge_pct"] = view.promoter_pct, view.pledge_pct

    def check(name, value, ok, hard=True, shown=None):
        res["checks"].append({"name": name, "ok": None if ok is None else bool(ok), "hard": hard,
                              "value": shown if shown is not None else value})

    pct = lambda v: "n/a" if v is None else f"{v:.0%}"  # noqa: E731
    check("Sales growth", m["rev_cagr"], None if m["rev_cagr"] is None else m["rev_cagr"] >= cfg.rev_cagr_min, shown=pct(m["rev_cagr"]) + " a year")
    check("Profit growth", m["pat_cagr"], None if m["pat_cagr"] is None else m["pat_cagr"] >= cfg.pat_cagr_min, shown=pct(m["pat_cagr"]) + " a year")
    check("No loss-making year", m["loss_years"], m["loss_years"] == 0, shown=f"{m['loss_years']} loss years")
    if view.is_financial:
        check("ROE above 15% (lender)", m["roe"], None if m["roe"] is None else m["roe"] >= cfg.roe_min_fin, shown=pct(m["roe"]))
    else:
        check("ROCE above 15%", m["roce"], None if m["roce"] is None else m["roce"] >= cfg.roce_min, shown=pct(m["roce"]))
        check("Low debt", m["debt_equity"], None if m["debt_equity"] is None else m["debt_equity"] <= cfg.de_max,
              shown="n/a" if m["debt_equity"] is None else f"{m['debt_equity']:.2f}x equity")
        check("Cash flow backs profit", m["cfo_to_pat"], None if m["cfo_to_pat"] is None else m["cfo_to_pat"] >= cfg.cfo_pat_min,
              shown="n/a" if m["cfo_to_pat"] is None else f"{m['cfo_to_pat']:.0%} of profit arrived as cash")
    check("Promoter holding", m["promoter_pct"], None if m["promoter_pct"] is None else m["promoter_pct"] >= cfg.promoter_min,
          hard=False, shown="unknown" if m["promoter_pct"] is None else pct(m["promoter_pct"]))
    check("Promoter pledging", m["pledge_pct"], None if m["pledge_pct"] is None else m["pledge_pct"] <= cfg.pledge_max,
          hard=m["pledge_pct"] is not None, shown="unknown" if m["pledge_pct"] is None else pct(m["pledge_pct"]))

    hard = [c for c in res["checks"] if c["hard"]]
    # A hard check with no data (None) is a fail: we do not buy what we cannot verify.
    res["passed"] = all(c["ok"] is True for c in hard)
    res["score"] = round(100 * sum(c["ok"] is True for c in hard) / len(hard))
    return res
