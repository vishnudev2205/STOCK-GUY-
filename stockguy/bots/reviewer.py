"""Bot 6: Reviewer. Once a quarter, asks of every holding: "is the reason I bought still true?"

It does not react to price moves. A falling price is not a reason to sell; a
broken business is. It sells when:
  - the company stopped trading (suspended/delisted)
  - it made a loss, returns fell below 12%, or debt went above 1x equity
  - it fails the Business Analyst's checks two reviews in a row
  - the price became extreme (PE over 2x its own usual AND PEG over 3), after year one
  - 5 years are up and it would not qualify as a fresh buy today
Everything is written to the journal in plain English.
"""
from __future__ import annotations

import pandas as pd

from ..config import Config


def _pct(v):
    return "n/a" if v is None else f"{v:.0%}"


def run(ledger, assessments: dict, prices: dict, asof, cfg: Config) -> list[dict]:
    asof = pd.Timestamp(asof)
    out = []
    for sym, pos in list(ledger.positions.items()):
        a = assessments.get(sym)
        th = pos.get("thesis", {})
        first_buy = pd.Timestamp(pos["lots"][0]["date"])
        held_years = (asof - first_buy).days / 365.25
        price = prices.get(sym)
        avg = ledger.avg_price(sym)
        change = (price / avg - 1) if price else None
        d = {"date": str(asof.date()), "symbol": sym, "name": pos.get("name", sym), "action": "HOLD", "reasons": []}
        why = f"Bought {first_buy:%b %Y} because: {th.get('summary', 'no reason recorded')}"

        if a is None or a.get("stale"):
            d["action"] = "SELL"
            d["writeoff"] = True
            d["reasons"].append("The stock has stopped trading. Written off at a haircut.")
        else:
            m = a["analyst"].get("metrics", {})
            fin = a.get("is_financial")
            ret_now = m.get("roe") if fin else m.get("roce")
            ret_then = th.get("ret")
            now = (f"Today: return on capital {_pct(ret_now)} (was {_pct(ret_then)}), "
                   f"profit growth {_pct(m.get('pat_cagr'))} a year (was {_pct(th.get('pat_cagr'))}), "
                   f"passes {a['judge'].get('n_passed', 0)} of 4 frameworks (was {th.get('n_frameworks', '?')}).")
            d["now"] = now
            if not m:
                d["reasons"].append("No usable financial results any more.")
            else:
                if m.get("latest_pat", 0) <= 0:
                    d["reasons"].append("It made a loss in the latest year.")
                if ret_now is not None and ret_now < cfg.thesis_roce_floor:
                    d["reasons"].append(f"Return on capital fell to {_pct(ret_now)}, below the {_pct(cfg.thesis_roce_floor)} floor.")
                de = m.get("debt_equity")
                if not fin and de is not None and de > cfg.thesis_de_ceiling:
                    d["reasons"].append(f"Debt rose to {de:.1f}x equity.")
            if m and not a["analyst"]["passed"]:
                failed = [c["name"].lower() for c in a["analyst"]["checks"] if c["hard"] and c["ok"] is not True]
                d["failed"] = ", ".join(failed)
                pos["strikes"] = pos.get("strikes", 0) + 1
                if pos["strikes"] >= 2:
                    d["reasons"].append(f"Failed the business checks two reviews in a row ({d['failed']}).")
            else:
                pos["strikes"] = 0
            v = a["valuation"]
            if (held_years >= 1 and v.get("pe") and v.get("pe_median") and v.get("peg")
                    and v["pe"] > cfg.extreme_pe_mult * v["pe_median"] and v["peg"] > cfg.extreme_peg):
                d["reasons"].append(f"Price is extreme: PE {v['pe']:.0f} vs its usual {v['pe_median']:.0f}, PEG {v['peg']:.1f}.")
            if held_years >= cfg.max_hold_years and not a.get("qualifies"):
                d["reasons"].append(f"Held {held_years:.1f} years (the plan was 3-5) and it would not qualify as a new buy today.")
            if d["reasons"]:
                d["action"] = "SELL"
            elif pos.get("strikes", 0) == 1:
                d["action"] = "WATCH"

        perf = "" if change is None else f" Paper position is {'up' if change >= 0 else 'down'} {abs(change):.0%}."
        if d["action"] == "SELL":
            d["text"] = f"SELL {d['name']}. {why}. That reason no longer holds: " + " ".join(d["reasons"]) + perf
        elif d["action"] == "WATCH":
            d["text"] = (f"WATCH {d['name']}. {why}. {d.get('now', '')} It slipped on the business checks this quarter ({d.get('failed', '')}). "
                         "One more quarter like this and it will be sold." + perf)
        else:
            d["text"] = f"HOLD {d['name']}. {why}. {d.get('now', '')} The original reason still holds." + perf
        out.append(d)
    return out
