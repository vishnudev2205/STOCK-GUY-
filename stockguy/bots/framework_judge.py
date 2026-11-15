"""Bot 3: Framework Judge. Scores a company the way four Indian investors might.

A stock must pass at least two of the four.

These investors work with judgement: management quality, the size of the
opportunity, the feel of a business. A program cannot do that. What follows are
number-based stand-ins for each idea, each worth 25 points, pass mark 60/100.
Treat a "pass" as "worth your reading time", not as their endorsement.
"""
from __future__ import annotations

from ..config import (Config, sector_has, CONSUMER_WORDS, INDIA_THEME_WORDS, BIG_RUNWAY_WORDS)


def _step(v, levels):
    """levels: [(threshold, points), ...] highest first; value must be >= threshold."""
    if v is None:
        return 0
    for th, pts in levels:
        if v >= th:
            return pts
    return 0


def _step_low(v, levels):
    """Lower is better."""
    if v is None:
        return 0
    for th, pts in levels:
        if v <= th:
            return pts
    return 0


def run(view, analyst: dict, val: dict, market: dict, cfg: Config) -> dict:
    """market: {"size_rank": 1 = biggest company in the universe, "n": universe size, "median_revenue": ...}"""
    m = analyst.get("metrics", {})
    if not m:
        return {"n_passed": 0, "passed": False, "frameworks": {}}
    ret = m.get("roe") if view.is_financial else m.get("roce")
    de = 0.0 if view.is_financial else m.get("debt_equity")
    peg, pe, pe_med, peer = val.get("peg"), val.get("pe"), val.get("pe_median"), val.get("peer_pe")
    no_loss = m.get("loss_years", 1) == 0
    out = {}

    # --- Raamdeo Agrawal: QGLP -------------------------------------------------
    p = {
        "Quality": _step(ret, [(0.20, 25), (0.15, 18), (0.12, 8)]),
        "Growth": _step(m.get("pat_cagr"), [(0.20, 25), (0.15, 18), (0.10, 8)]),
        "Longevity": (25 if no_loss and (m.get("profit_up_share") or 0) >= 0.75 else
                      18 if no_loss and (m.get("profit_up_share") or 0) >= 0.6 else 0),
        "Price": _step_low(peg, [(1.0, 25), (1.5, 18), (2.0, 8)]) if peg and peg > 0 else 0,
    }
    out["QGLP (Raamdeo Agrawal)"] = _verdict(p, cfg, must=list(p))   # he wants all four present

    # --- Vijay Kedia: SMILE ----------------------------------------------------
    rank = market.get("size_rank")
    small_rev = (m.get("latest_revenue") or 0) < (market.get("median_revenue") or 0)
    p = {
        "Small size": 0 if rank is None else 25 if rank > 250 else 15 if rank > 100 else 0,
        "Medium experience": (25 if view.listed_years >= 5 and no_loss else 12 if view.listed_years >= 3 else 0),
        "Large aspiration": max(_step(m.get("rev_cagr"), [(0.20, 25), (0.15, 18), (0.12, 8)]),
                                _step(m.get("asset_cagr"), [(0.20, 18), (0.15, 12)])),
        "Extra-large market": (15 if sector_has(view.sector, BIG_RUNWAY_WORDS) else 0) + (10 if small_rev else 0),
    }
    out["SMILE (Vijay Kedia)"] = _verdict(p, cfg, must=["Small size"])

    # --- Rakesh Jhunjhunwala: growth at a fair price ---------------------------
    p = {
        "Growth": _step(m.get("pat_cagr"), [(0.18, 25), (0.15, 18), (0.12, 8)]),
        "Fair price": _step_low(peg, [(1.2, 25), (1.5, 18), (2.0, 8)]) if peg and peg > 0 else 0,
        "India growth theme": 25 if sector_has(view.sector, INDIA_THEME_WORDS) else 0,
        "Conviction quality": (25 if (m.get("roe") or 0) >= 0.15 and (de is not None and de <= 1.0) else
                               12 if (m.get("roe") or 0) >= 0.12 else 0),
    }
    out["GARP (Rakesh Jhunjhunwala)"] = _verdict(p, cfg, must=["Growth", "Fair price"])

    # --- Radhakishan Damani: simple consumer business, value, patience ---------
    cheap_ref = min([x for x in (pe_med, peer) if x], default=None)
    wobble = m.get("margin_wobble")
    p = {
        "Consumer business": 25 if sector_has(view.sector, CONSUMER_WORDS) else 0,
        "Simple and steady": (25 if no_loss and wobble is not None and wobble <= 0.25 else
                              12 if no_loss and wobble is not None and wobble <= 0.40 else 0),
        "Value": (0 if not pe or not cheap_ref else 25 if pe <= cheap_ref else 12 if pe <= 1.2 * cheap_ref else 0),
        "Staying power": (25 if de is not None and de <= 0.3 and (m.get("fcf_positive_share") or 0) >= 0.6 else
                          12 if de is not None and de <= 0.5 else 0),
    }
    out["Value (Radhakishan Damani)"] = _verdict(p, cfg, must=["Consumer business"])

    n = sum(f["passed"] for f in out.values())
    return {"frameworks": out, "n_passed": n, "passed": n >= cfg.min_frameworks,
            "passed_names": [k for k, f in out.items() if f["passed"]]}


def _verdict(parts: dict, cfg: Config, must=()):
    score = sum(parts.values())
    return {"score": score, "parts": parts,
            "passed": score >= cfg.framework_pass_score and all(parts[k] > 0 for k in must)}
