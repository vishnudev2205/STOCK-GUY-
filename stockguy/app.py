"""Glue for the commands in run.py. All state is plain JSON under data/ ."""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from .backtest import run_backtest
from .bots import scout, risk_manager
from .config import Config
from .ledger import Ledger
from .pipeline import assess, quarterly_review


def _load(p: Path, default):
    try:
        return json.loads(p.read_text()) if p.exists() else default
    except Exception:  # noqa: BLE001
        return default


def _save(p: Path, obj):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, indent=1, default=_json))


def _json(o):
    if hasattr(o, "item"):
        return o.item()
    return str(o)


def make_provider(cfg: Config, demo=False, offline=False):
    if demo:
        from .synthetic import SyntheticData
        return SyntheticData(cfg)
    from .data import YFinanceData
    return YFinanceData(cfg, offline=offline)


def preload(provider, log=print):
    syms = provider.all_symbols()
    log(f"Loading data for {len(syms)} companies (every company that was ever in the index lists we have).")
    log("First run downloads everything and can take 30-60 minutes; later runs use the cache.")
    for i, s in enumerate(syms, 1):
        provider.stock(s)
        if i % 50 == 0:
            log(f"  {i}/{len(syms)}")


def cmd_backtest(provider, cfg: Config, years=None, log=print) -> dict:
    log(f"\n== BACKTEST ({provider.label}) ==")
    rep = run_backtest(provider, cfg, years=years, log=log)
    rep["run_at"] = pd.Timestamp.now().isoformat(timespec="minutes")
    rep["data_notes"] = provider.notes[:40]
    _save(cfg.data_dir / "backtest_report.json", rep)
    print_report(rep, log)
    return rep


def print_report(rep: dict, log=print):
    log("\n" + "=" * 72)
    if not rep.get("ok"):
        log("BACKTEST COULD NOT RUN\n" + rep["problem"])
        return
    s, i = rep["strategy"], rep["index_fund"]
    p = lambda v: "n/a" if v is None else f"{v:+.1%}"  # noqa: E731
    log(f"VERDICT: {rep['headline']}")
    log(f"Period tested: {rep['start']} to {rep['end']} ({rep['tested_years']} years; you asked for {rep['requested_years']})")
    log(f"{'':28}{'These rules':>14}{'Index lump sum':>16}{'Index SIP':>12}")
    log(f"{'Return per year':28}{p(s['cagr']):>14}{p(i['lump_cagr']):>16}{p(i['sip_xirr']):>12}")
    log(f"{'Rs ' + format(rep.get('capital', 0), ',.0f') + ' became':28}{s['final_after_exit_tax']:>14,.0f}{i['lump_final']:>16,.0f}{i['sip_final']:>12,.0f}")
    log(f"{'Max drawdown':28}{p(s['max_drawdown']):>14}{p(i['lump_max_drawdown']):>16}")
    log(f"{'Worst year':28}{str(s['worst_year']) + ' ' + p(s['worst_year_return']):>14}"
        f"{str(i['lump_worst_year']) + ' ' + p(i['lump_worst_year_return']):>16}")
    u = lambda v: "n/a" if v is None else f"{v:.0%}"  # noqa: E731
    log(f"Hit rate: {u(s['hit_rate'])} of {s['purchases']} purchases made money; {u(s['beat_index_rate'])} beat the index.")
    log(f"Charges paid Rs {s['charges_paid']:,.0f}, tax paid Rs {s['tax_paid']:,.0f}, "
        f"tax still due on exit Rs {s['exit_tax_and_charges']:,.0f}.")
    log("\nRead these before trusting the numbers:")
    for c in rep["caveats"]:
        log("  - " + c)
    log("=" * 72)


def slim(a: dict, held: set) -> dict:
    if a.get("stale"):
        return {"symbol": a["symbol"], "name": a["name"], "sector": a["sector"], "stage": a["stage"], "held": a["symbol"] in held}
    m, v = a["analyst"].get("metrics", {}), a["valuation"]
    return {"symbol": a["symbol"], "name": a["name"], "sector": a["sector"], "price": a["price"],
            "stage": a["stage"], "qualifies": a["qualifies"], "score": a["composite"], "held": a["symbol"] in held,
            "triggers": a["triggers"], "business": a["analyst"]["score"], "years": a["analyst"]["years"],
            "checks": a["analyst"]["checks"], "notes": a["analyst"]["notes"],
            "frameworks": {k: {"score": f["score"], "passed": f["passed"], "parts": f["parts"]}
                           for k, f in a["judge"].get("frameworks", {}).items()},
            "n_frameworks": a["judge"].get("n_passed", 0),
            "pe": v.get("pe"), "pe_median": v.get("pe_median"), "peer_pe": v.get("peer_pe"), "peg": v.get("peg"),
            "price_ok": v.get("ok"), "price_reasons": v.get("reasons", []),
            "rev_cagr": m.get("rev_cagr"), "pat_cagr": m.get("pat_cagr"),
            "roce": m.get("roe") if a.get("is_financial") else m.get("roce"), "debt_equity": m.get("debt_equity")}


def cmd_scout(provider, cfg: Config, live_events=True, log=print) -> list[dict]:
    """Weekly. Refreshes triggers and the scored watchlist. Never trades."""
    today = provider.calendar()[-1]
    log(f"\n== SCOUT (data up to {today.date()}) ==")
    ev_path = cfg.data_dir / "events.json"
    events, problems = _load(ev_path, {}), []
    if live_events:
        uni = provider.universe(today)
        new, problems = scout.fetch_events(dict(zip(uni["symbol"], uni["name"])))
        for s, rows in new.items():
            have = {(e["type"], e["detail"]) for e in events.get(s, [])}
            events.setdefault(s, []).extend(e for e in rows if (e["type"], e["detail"]) not in have)
    cutoff = str((today - pd.Timedelta(days=cfg.trigger_lookback_days)).date())
    events = {s: [e for e in rows if (e.get("date") or "9999") >= cutoff] for s, rows in events.items()}
    events = {s: r for s, r in events.items() if r}
    _save(ev_path, events)
    ledger = Ledger(cfg, cfg.data_dir / "ledger.json")
    A, stats = assess(provider, today, cfg, extra_symbols=list(ledger.positions), events=events)
    held = set(ledger.positions)
    _save(cfg.data_dir / "watchlist.json", {"asof": str(today.date()), "stats": stats, "problems": problems,
                                             "source": provider.label, "rows": [slim(a, held) for a in A]})
    for pr in problems:
        log("  ! " + pr)
    log(f"  {stats['members']} index members, {stats['analysable']} with enough history, "
        f"{stats['candidates']} buy candidates this week.")
    return A


def review_due(cfg: Config, today) -> bool:
    st = _load(cfg.data_dir / "state.json", {})
    last = st.get("last_review")
    if not last:
        return True
    q = lambda d: (d.year, (d.month - 1) // 3)  # noqa: E731
    lastd = pd.Timestamp(last)
    return today.month in cfg.review_months and (today.year, today.month) != (lastd.year, lastd.month) \
        or (today - lastd).days > 100 and q(today) != q(lastd)


def cmd_review(provider, cfg: Config, A=None, force=False, log=print):
    """Quarterly. Reviewer then Risk Manager, on the PAPER ledger only."""
    today = provider.calendar()[-1]
    rep = _load(cfg.data_dir / "backtest_report.json", None)
    if not rep or not rep.get("ok"):
        log("\n== REVIEW skipped: the backtest has not run successfully yet. Tests come first. ==")
        return
    if not force and not review_due(cfg, today):
        log("\n== REVIEW not due (once a quarter). Use `python run.py review --force` to run it anyway. ==")
        return
    log(f"\n== QUARTERLY REVIEW ({today.date()}) - paper ledger only ==")
    ledger = Ledger(cfg, cfg.data_dir / "ledger.json")
    if A is None:
        A, _ = assess(provider, today, cfg, extra_symbols=list(ledger.positions), events=_load(cfg.data_dir / "events.json", {}))
    prices = {a["symbol"]: a["trade_price"] for a in A if not a.get("stale") and a.get("trade_price")}
    real = risk_manager.load_real_holdings(cfg)
    entries = quarterly_review(ledger, A, prices, today, cfg, real=real)
    ledger.save()
    jp = cfg.data_dir / "journal.json"
    journal = _load(jp, []) + entries
    _save(jp, journal)
    write_journal_md(cfg, journal)
    _save(cfg.data_dir / "state.json", {"last_review": str(today.date())})
    for e in entries:
        if e["action"] != "HOLD":
            log("  " + e["text"])
    log(f"  {sum(e['action'] == 'HOLD' for e in entries)} holdings unchanged. Paper cash Rs {ledger.cash:,.0f}.")


def write_journal_md(cfg: Config, journal: list[dict]):
    lines = ["# Journal (paper portfolio)", ""]
    for day in sorted({e["date"] for e in journal}, reverse=True):
        lines += [f"## {day}", ""] + [f"- {e['text']}" for e in journal if e["date"] == day] + [""]
    (cfg.data_dir / "journal.md").write_text("\n".join(lines))


def portfolio_snapshot(provider, cfg: Config) -> dict:
    ledger = Ledger(cfg, cfg.data_dir / "ledger.json")
    today = provider.calendar()[-1]
    rows, prices = [], {}
    for s, pos in ledger.positions.items():
        v = provider.view(s, today)
        p = v.trade_price if v is not None and v.has_price else None
        if p:
            prices[s] = p
    eq = ledger.equity(prices)
    for s, pos in ledger.positions.items():
        p, avg, q = prices.get(s), ledger.avg_price(s), ledger.qty(s)
        rows.append({"symbol": s, "name": pos["name"], "sector": pos["sector"], "qty": q, "avg": avg, "price": p,
                     "value": (p or 0) * q, "weight": (p or 0) * q / eq if eq else 0,
                     "pnl": (p / avg - 1) if p else None, "bought": pos["lots"][0]["date"],
                     "why": pos.get("thesis", {}).get("summary", ""), "strikes": pos.get("strikes", 0)})
    rows.sort(key=lambda r: -r["value"])
    sectors = {}
    for r in rows:
        sectors[r["sector"]] = sectors.get(r["sector"], 0) + r["weight"]
    real = risk_manager.load_real_holdings(cfg)
    return {"asof": str(today.date()), "cash": ledger.cash, "equity": eq, "start": ledger.start_capital,
            "rows": rows, "sectors": sectors, "trades": ledger.trades[-60:], "tax_paid": ledger.tax_paid,
            "charges": ledger.charges_total, "overlap": risk_manager.overlap_report(ledger, real),
            "limits": {"stock": cfg.max_stock_w, "sector": cfg.max_sector_w, "min": cfg.min_stocks, "max": cfg.max_stocks}}
