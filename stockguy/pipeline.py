"""Runs the six bots in order for one date. The backtest and the live paper run
both call exactly this code, so what was tested is what runs."""
from __future__ import annotations

import numpy as np
import pandas as pd

from .config import Config
from .bots import scout, business_analyst, framework_judge, valuation_analyst, risk_manager, reviewer


def assess(provider, asof, cfg: Config, extra_symbols=(), events: dict | None = None) -> tuple[list[dict], dict]:
    """Bots 1-4 for every index member on `asof`. Returns (assessments, coverage stats)."""
    asof = pd.Timestamp(asof)
    uni = provider.universe(asof)
    members = list(uni["symbol"])
    symbols = members + [s for s in extra_symbols if s not in set(members)]
    views, no_data = {}, []
    for s in symbols:
        v = provider.view(s, asof)
        if v is None or not v.has_price:
            no_data.append(s)
        else:
            views[s] = v
    live = {s: v for s, v in views.items() if not v.stale}
    analysis = {s: business_analyst.run(v, cfg) for s, v in live.items()}

    # peers and size, measured only on what was known on this date
    pes, mcaps, revs = {}, {}, []
    for s, v in live.items():
        if v.pe and 0 < v.pe < 200:
            pes.setdefault(v.sector, []).append(v.pe)
        if v.mcap:
            mcaps[s] = v.mcap
        r = analysis[s]["metrics"].get("latest_revenue")
        if r:
            revs.append(r)
    peer_pe = {sec: float(np.median(x)) for sec, x in pes.items() if len(x) >= 3}
    rank = {s: i + 1 for i, s in enumerate(sorted(mcaps, key=mcaps.get, reverse=True))}
    market = {"n": len(live), "median_revenue": float(np.median(revs)) if revs else None}

    out = []
    for s, v in views.items():
        if s not in live:
            out.append({"symbol": s, "name": v.name, "sector": v.sector, "stale": True, "qualifies": False,
                        "stage": "Stopped trading", "price": v.price, "trade_price": v.trade_price})
            continue
        an = analysis[s]
        val = valuation_analyst.run(v, an, peer_pe.get(v.sector), cfg)
        jd = framework_judge.run(v, an, val, {**market, "size_rank": rank.get(s)}, cfg)
        trig = scout.run(v, cfg, (events or {}).get(s))
        if not an["metrics"]:
            stage = "Not enough history"
        elif not an["passed"]:
            failed = [c["name"] for c in an["checks"] if c["hard"] and c["ok"] is not True]
            stage = "Business check failed: " + ", ".join(failed)
        elif not jd["passed"]:
            stage = f"Passes only {jd['n_passed']} of 4 frameworks"
        elif not val["ok"]:
            stage = "Good company, wrong price"
        elif not trig:
            stage = "Qualifies, waiting for a trigger"
        else:
            stage = "BUY CANDIDATE"
        fw_scores = [f["score"] for f in jd.get("frameworks", {}).values()]
        out.append({
            "symbol": s, "name": v.name, "sector": v.sector, "stale": False, "is_financial": v.is_financial,
            "price": v.price, "trade_price": v.trade_price, "mcap": v.mcap, "in_index": s in set(members),
            "triggers": trig, "analyst": an, "judge": jd, "valuation": val, "stage": stage,
            "qualifies": stage == "BUY CANDIDATE",
            "composite": round(0.3 * an["score"] + 0.4 * (np.mean(fw_scores) if fw_scores else 0) + 0.3 * val["score"], 1),
        })
    out.sort(key=lambda a: (-int(a.get("qualifies", False)), -a.get("composite", 0)))
    yrs = [a["years"] for a in analysis.values()]
    stats = {"date": str(asof.date()), "members": len(members), "no_data": len(no_data), "no_data_symbols": no_data,
             "analysable": int(sum(y >= cfg.min_years for y in yrs)),
             "median_years": float(np.median(yrs)) if yrs else 0.0,
             "candidates": int(sum(a.get("qualifies", False) for a in out))}
    return out, stats


def thesis_for(a: dict) -> dict:
    m = a["analyst"]["metrics"]
    ret = m.get("roe") if a.get("is_financial") else m.get("roce")
    v = a["valuation"]
    f = lambda x: "n/a" if x is None else f"{x:.0%}"  # noqa: E731
    names = [n.split(" (")[0] for n in a["judge"]["passed_names"]]
    summary = (f"{'; '.join(t['type'].lower() for t in a['triggers'])}. Profit growing {f(m.get('pat_cagr'))} a year, "
               f"return on capital {f(ret)}, passes {' and '.join(names)}, PE {v['pe']:.0f}"
               + (f" vs its usual {v['pe_median']:.0f}" if v.get("pe_median") else ""))
    return {"summary": summary, "ret": ret, "pat_cagr": m.get("pat_cagr"), "rev_cagr": m.get("rev_cagr"),
            "debt_equity": m.get("debt_equity"), "n_frameworks": a["judge"]["n_passed"],
            "frameworks": a["judge"]["passed_names"], "pe": v.get("pe"), "peg": v.get("peg"),
            "triggers": a["triggers"]}


def quarterly_review(ledger, assessments: list[dict], prices: dict, exec_date, cfg: Config,
                     real: dict | None = None, fmv_2018=None) -> list[dict]:
    """Bots 6 then 5, writing to the paper ledger. `prices` are the prices trades happen at.
    Returns journal entries in plain English."""
    by = {a["symbol"]: a for a in assessments}
    day = str(pd.Timestamp(exec_date).date())
    journal = []
    fmv = fmv_2018 or (lambda s: None)
    for t in ledger.settle_tax(exec_date):
        if t["tax"] > 0:
            journal.append({"date": day, "symbol": "", "action": "TAX",
                            "text": f"Paid Rs {t['tax']:,.0f} capital gains tax for FY {t['fy']} from paper cash."})
    for d in reviewer.run(ledger, by, prices, exec_date, cfg):
        sym = d["symbol"]
        if d["action"] == "SELL":
            if d.get("writeoff") or not prices.get(sym):
                last = by.get(sym, {}).get("trade_price") or ledger.avg_price(sym)
                ledger.sell(sym, None, last * (1 - cfg.delist_haircut), exec_date, "Stopped trading (written off)", fmv(sym))
            else:
                ledger.sell(sym, None, prices[sym], exec_date, "; ".join(d["reasons"]), fmv(sym))
        journal.append({k: d[k] for k in ("date", "symbol", "action", "text")} | {"date": day})
    for t in risk_manager.trims(ledger, prices, cfg) + risk_manager.cash_calls(ledger, prices):
        if ledger.sell(t["symbol"], t["qty"], prices[t["symbol"]], exec_date, t["reason"], fmv(t["symbol"])):
            journal.append({"date": day, "symbol": t["symbol"], "action": "TRIM", "text": f"TRIM {t['symbol']}. {t['reason']}."})
    cands = [a for a in assessments if a.get("qualifies")]
    orders, notes = risk_manager.plan_buys(ledger, cands, prices, cfg, real)
    for o in orders:
        a = by[o["symbol"]]
        th = thesis_for(a)
        if ledger.buy(a["symbol"], o["qty"], prices[a["symbol"]], exec_date, a["name"], a["sector"], th):
            journal.append({"date": day, "symbol": a["symbol"], "action": "BUY",
                            "text": f"BUY {a['name']} ({o['weight']:.1%} of the portfolio). Why: {th['summary']}."})
    for n in notes:
        journal.append({"date": day, "symbol": "", "action": "NOTE", "text": n})
    return journal
