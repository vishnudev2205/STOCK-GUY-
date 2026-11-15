"""Backtest: would these rules have worked, with real-world frictions?

What it does to stay honest:
  1. No look-ahead. Decisions on day D use a view of the data cut off at D
     (results count only after their publication lag). Trades happen at the
     NEXT trading day's close.
  2. No survivorship bias, as far as the data allows. The universe on each date
     is the Nifty 500 list as it was then. Members we have no data for are
     counted and reported. A holding that stops trading is written off at a haircut.
  3. Costs. STT, exchange and SEBI charges, GST, stamp duty, DP charges,
     slippage, and capital gains tax under the rules in force on each sale date.
  4. Benchmark. A Nifty 50 index fund: monthly SIP (XIRR) and lump sum (CAGR),
     both after exit tax, same period.
  5. Says so plainly if the index fund wins.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import costs
from .config import Config
from .ledger import Ledger
from .pipeline import assess, quarterly_review


def review_dates(cal: pd.DatetimeIndex, start, end, cfg: Config) -> list[pd.Timestamp]:
    c = cal[(cal >= start) & (cal <= end)]
    s = pd.Series(c, index=c)
    last = s.groupby([c.year, c.month]).last()
    return [d for d in last if d.month in cfg.review_months]


def xirr(flows: list[tuple[pd.Timestamp, float]]) -> float | None:
    if not flows or not (any(f < 0 for _, f in flows) and any(f > 0 for _, f in flows)):
        return None
    t0 = flows[0][0]
    yrs = np.array([(d - t0).days / 365.25 for d, _ in flows])
    amt = np.array([f for _, f in flows])
    lo, hi = -0.95, 5.0
    npv = lambda r: float((amt / (1 + r) ** yrs).sum())  # noqa: E731
    if npv(lo) * npv(hi) > 0:
        return None
    for _ in range(200):
        mid = (lo + hi) / 2
        if npv(lo) * npv(mid) <= 0:
            hi = mid
        else:
            lo = mid
    return (lo + hi) / 2


def curve_stats(eq: pd.Series) -> dict:
    dd = eq / eq.cummax() - 1
    by_year = eq.groupby(eq.index.year).last()
    prev = by_year.shift(1)
    prev.iloc[0] = eq.iloc[0]
    yr = (by_year / prev - 1)
    trough = dd.idxmin()
    return {"max_drawdown": float(dd.min()), "drawdown_trough": str(trough.date()),
            "drawdown_peak": str(eq.loc[:trough].idxmax().date()),
            "worst_year": int(yr.idxmin()), "worst_year_return": float(yr.min()),
            "yearly": {int(k): float(v) for k, v in yr.items()}}


def index_fund(bench: pd.Series, start, end, cfg: Config) -> dict:
    """Same money in a Nifty 50 index fund: once as a lump sum, once as a monthly SIP."""
    b = bench.loc[start:end].dropna()
    years = (b.index[-1] - b.index[0]).days / 365.25
    stamp = 0.00005
    # lump sum
    units = cfg.capital * (1 - stamp) / b.iloc[0]
    curve = units * b
    rows = [{"buy_date": b.index[0], "sell_date": b.index[-1], "gain": curve.iloc[-1] - cfg.capital}]
    tax, _, _ = costs.tax_for_year(rows, {}, cfg)
    lump_final = curve.iloc[-1] - tax
    # SIP: equal instalments on the first trading day of every month
    firsts = pd.Series(b.index, index=b.index).groupby([b.index.year, b.index.month]).first()
    inst = cfg.capital / len(firsts)
    flows, lots = [], []
    for d in firsts:
        u = inst * (1 - stamp) / b.loc[d]
        lots.append((d, u))
        flows.append((d, -inst))
    sip_value = sum(u for _, u in lots) * b.iloc[-1]
    rows = [{"buy_date": d, "sell_date": b.index[-1], "gain": u * b.iloc[-1] - inst} for d, u in lots]
    sip_tax, _, _ = costs.tax_for_year(rows, {}, cfg)
    flows.append((b.index[-1], sip_value - sip_tax))
    return {"years": years, "lump_curve": curve,
            "lump_final": float(lump_final), "lump_cagr": float((lump_final / cfg.capital) ** (1 / years) - 1),
            "sip_final": float(sip_value - sip_tax), "sip_invested": float(cfg.capital),
            "sip_xirr": xirr(flows), "sip_instalment": float(inst), **{"lump_" + k: v for k, v in curve_stats(curve).items()}}


def run_backtest(provider, cfg: Config, years: int | None = None, end=None, log=print) -> dict:
    years = years or cfg.backtest_years
    cal = provider.calendar()
    end = pd.Timestamp(end) if end is not None else cal[-1]
    want_start = end - pd.DateOffset(years=years)
    dates = review_dates(cal, want_start, end, cfg)
    ledger = Ledger(cfg)
    timeline, journal, coverage = [], [], []
    start = None
    no_data_all: set[str] = set()
    non_pit_dates = 0

    def fmv_lookup(asof):
        def f(sym):
            s = provider.stock(sym)
            if s is None:
                return None
            px = s.tr.loc[:min(pd.Timestamp("2018-01-31"), pd.Timestamp(asof))]
            return float(px.iloc[-1]) if len(px) and px.index[-1] >= pd.Timestamp("2018-01-01") else None
        return f

    for d in dates:
        later = cal[cal > d]
        if len(later) == 0:
            break
        exec_date = later[0]
        A, stats = assess(provider, d, cfg, extra_symbols=list(ledger.positions))
        if start is None:
            # Do not start the clock until the data can support the rules; idle cash would flatter nobody.
            if stats["analysable"] < max(20, 0.3 * stats["members"]):
                coverage.append({**stats, "skipped": True})
                continue
            start = exec_date
            timeline.append((exec_date, {}, ledger.cash))
        if getattr(provider, "universe_date", lambda _d: d)(d) is None:
            non_pit_dates += 1
        no_data_all.update(stats.pop("no_data_symbols"))
        coverage.append(stats)
        # Prices at the next close. Looking at exec_date here is execution, not decision-making.
        prices = {}
        for s in {a["symbol"] for a in A if a.get("qualifies")} | set(ledger.positions):
            v = provider.view(s, exec_date)
            if v is not None and v.has_price and not v.stale:
                prices[s] = v.trade_price
        journal += quarterly_review(ledger, A, prices, exec_date, cfg, fmv_2018=fmv_lookup(exec_date))
        timeline.append((exec_date, ledger.quantities(), ledger.cash))
        log(f"  {d.date()}  members {stats['members']:>3}  analysable {stats['analysable']:>3}  "
            f"candidates {stats['candidates']:>2}  holding {len(ledger.positions):>2}")

    if start is None:
        return {"ok": False, "label": provider.label, "requested_years": years, "coverage": coverage,
                "problem": ("Not one review date had enough published financial history to apply the rules. "
                            f"The rules need {cfg.min_years}+ years of annual results per company; the free data "
                            "source does not go back far enough. Add longer history (see README) and re-run.")}

    # ---- daily value of the paper portfolio -------------------------------
    days = cal[(cal >= start) & (cal <= end)]
    syms = sorted({s for _, q, _ in timeline for s in q})
    px = pd.DataFrame({s: provider.stock(s).tr.reindex(days, method="ffill") for s in syms}, index=days)
    eq = pd.Series(0.0, index=days)
    for i, (d0, qty, cash) in enumerate(timeline):
        d1 = timeline[i + 1][0] if i + 1 < len(timeline) else days[-1] + pd.Timedelta(days=1)
        seg = days[(days >= d0) & (days < d1)]
        val = sum((px.loc[seg, s].fillna(0.0) * q for s, q in qty.items()), start=pd.Series(0.0, index=seg))
        eq.loc[seg] = val + cash
    last_prices = {s: float(px[s].iloc[-1]) for s in ledger.positions if pd.notna(px[s].iloc[-1])}
    exit_cost = ledger.exit_tax_estimate(last_prices, days[-1])
    final_before, final_after = float(eq.iloc[-1]), float(eq.iloc[-1] - exit_cost)
    n_years = (days[-1] - start).days / 365.25
    cagr = (final_after / cfg.capital) ** (1 / n_years) - 1 if n_years > 0 and final_after > 0 else None

    bench = provider.benchmark()
    idx = index_fund(bench, start, days[-1], cfg)

    # ---- hit rate: every purchase, closed or still open --------------------
    picks = [{"symbol": c["symbol"], "buy_date": c["buy_date"], "sell_date": c["sell_date"],
              "ret": c["sell_price"] / c["buy_price"] - 1, "open": False} for c in ledger.closed]
    for s, pos in ledger.positions.items():
        for lot in pos["lots"]:
            if s in last_prices:
                picks.append({"symbol": s, "buy_date": lot["date"], "sell_date": str(days[-1].date()),
                              "ret": last_prices[s] / lot["price"] - 1, "open": True})
    for p in picks:
        b0, b1 = bench.loc[:p["buy_date"]], bench.loc[:p["sell_date"]]
        p["index_ret"] = float(b1.iloc[-1] / b0.iloc[-1] - 1) if len(b0) and len(b1) else None
    hit = float(np.mean([p["ret"] > 0 for p in picks])) if picks else None
    beat = [p["ret"] > p["index_ret"] for p in picks if p["index_ret"] is not None]
    writeoffs = [c for c in ledger.closed if "written off" in c["reason"]]

    stats = curve_stats(eq)
    n_hold = [len(q) for _, q, _ in timeline[1:]]
    beats_lump = cagr is not None and cagr > idx["lump_cagr"]
    beats_sip = cagr is not None and idx["sip_xirr"] is not None and cagr > idx["sip_xirr"]
    if beats_lump and beats_sip:
        verdict, headline = "BEAT", "The rules beat the Nifty 50 index fund in this test, after all costs and tax."
    elif not beats_lump and not beats_sip:
        verdict, headline = "LOST", ("The rules did NOT beat the Nifty 50 index fund. After costs and tax, "
                                     "a plain index fund would have left you with more money.")
    else:
        verdict, headline = "MIXED", ("Mixed result: ahead of the index fund on one measure and behind on the other. "
                                      "That is not a clear win.")
    monthly = eq.resample("ME").last().dropna()
    lump_m = idx["lump_curve"].resample("ME").last().dropna()
    caveats = _caveats(provider, cfg, years, n_years, coverage, no_data_all, non_pit_dates, writeoffs, n_hold, picks)
    return {
        "ok": True, "label": provider.label, "synthetic": "SYNTHETIC" in provider.label,
        "requested_years": years, "tested_years": round(n_years, 2),
        "start": str(start.date()), "end": str(days[-1].date()),
        "verdict": verdict, "headline": headline,
        "strategy": {"final_value": final_before, "final_after_exit_tax": final_after, "cagr": cagr,
                     "max_drawdown": stats["max_drawdown"], "drawdown_peak": stats["drawdown_peak"],
                     "drawdown_trough": stats["drawdown_trough"], "worst_year": stats["worst_year"],
                     "worst_year_return": stats["worst_year_return"], "yearly": stats["yearly"],
                     "hit_rate": hit, "beat_index_rate": float(np.mean(beat)) if beat else None,
                     "purchases": len(picks), "trades": len(ledger.trades),
                     "charges_paid": ledger.charges_total, "tax_paid": float(sum(t["tax"] for t in ledger.tax_paid)),
                     "exit_tax_and_charges": float(exit_cost),
                     "avg_holdings": float(np.mean(n_hold)) if n_hold else 0.0,
                     "avg_cash_share": float(np.mean([c / eq.loc[d] for d, _, c in timeline[1:] if eq.loc[d] > 0])) if n_hold else 1.0,
                     "writeoffs": len(writeoffs)},
        "index_fund": {k: v for k, v in idx.items() if k != "lump_curve"},
        "curve": {"dates": [str(d.date()) for d in monthly.index], "strategy": [round(float(v)) for v in monthly],
                  "index": [round(float(v)) for v in lump_m.reindex(monthly.index, method="ffill")]},
        "caveats": caveats, "coverage": coverage,
        "closed": ledger.closed, "tax": ledger.tax_paid, "journal": journal,
    }


def _caveats(provider, cfg, years, n_years, coverage, no_data, non_pit, writeoffs, n_hold, picks) -> list[str]:
    out = []
    if "SYNTHETIC" in provider.label:
        out.append("THIS RUN USED MADE-UP TEST DATA. It proves the machinery works. It says nothing about real stocks.")
    if n_years < years - 0.75:
        out.append(f"You asked for {years} years; only {n_years:.1f} years could be tested. Before that, too few "
                   f"companies had {cfg.min_years}+ years of published results in the free data. "
                   f"A {n_years:.1f}-year test is weak evidence for a 3-5 year holding strategy.")
    used = [c for c in coverage if not c.get("skipped")]
    if used:
        med = float(np.median([c["median_years"] for c in used]))
        if med < cfg.ideal_years:
            out.append(f"Typical company had only {med:.0f} years of financial history at decision time "
                       "(the Business Analyst is meant to see 5-10).")
    if not provider.point_in_time_universe:
        out.append("SURVIVORSHIP BIAS PRESENT: historical Nifty 500 member lists could not be loaded, so today's "
                   "list was used for past dates. Companies that failed are missing. Results are too rosy.")
    elif non_pit:
        out.append(f"{non_pit} early review dates had no index list old enough; the earliest available list was used for them.")
    if no_data:
        out.append(f"{len(no_data)} companies that were in the index at some point have no price data from the free "
                   "source (mostly delisted, merged or renamed). The rules could never pick them, so their failures "
                   "are not in the result. This is a remaining survivorship leak.")
    if writeoffs:
        out.append(f"{len(writeoffs)} holding(s) stopped trading while held and were written off at a "
                   f"{cfg.delist_haircut:.0%} haircut to the last price (an assumption, not a fact).")
    out.append("Promoter holding and pledging were NOT tested: free history for them does not exist.")
    out.append("Of the Scout's triggers, only results and debt reduction could be tested. CEO changes, M&A, orders, "
               "capacity and promoter buying have no free historical archive.")
    if n_hold and np.mean(n_hold) < cfg.min_stocks:
        out.append(f"The portfolio averaged {np.mean(n_hold):.0f} stocks, below the 15-20 target, because few "
                   "companies passed every check. Cash sat idle earning nothing in this test.")
    if len(picks) < 30:
        out.append(f"Only {len(picks)} purchases in the whole test. Too few to separate skill from luck.")
    out.append("Dividends are assumed reinvested with no dividend tax. Surcharge on tax is ignored.")
    out.append("One backtest of one rule set is not proof. Past results do not predict future returns.")
    return out
