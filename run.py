#!/usr/bin/env python3
"""Stock Guy - one command:   python run.py

  python run.py              do everything that is due, then build the dashboard
  python run.py backtest     the 10-year test against a Nifty 50 index fund
  python run.py scout        weekly: refresh triggers and the scored watchlist
  python run.py review       quarterly: re-check holdings, update the paper portfolio
  python run.py buy          start now: paper-buy today's candidates at today's real prices
  python run.py cashout      show what you would get if you sold everything today
                             (add --confirm to actually close the paper portfolio)
  python run.py trades       every paper buy and sell: time, price, reason
  python run.py holdings     read your Zerodha holdings (read-only)  [--csv file.csv]
  python run.py dashboard    rebuild dashboard/index.html
  python run.py doctor       check which free data sources are reachable
  python run.py test         run the safety tests

  --demo     use made-up test data (no internet needed; results are meaningless)
  --offline  use only what is already cached

PAPER MODE ONLY. Nothing in this project can place a real order.
"""
import argparse
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

from stockguy import app
from stockguy.config import Config, DATA, ROOT


def doctor():
    import requests
    hosts = {"Yahoo Finance (prices, financials)": "https://query1.finance.yahoo.com/v8/finance/chart/RELIANCE.NS?range=5d",
             "NSE archives (Nifty 500 list)": "https://nsearchives.nseindia.com/content/indices/ind_nifty500list.csv",
             "NSE site (filings, insider trades)": "https://www.nseindia.com/",
             "Wayback Machine (old index lists)": "https://web.archive.org/cdx/search/cdx?url=nseindia.com&limit=1",
             "Economic Times RSS": "https://economictimes.indiatimes.com/markets/stocks/rssfeeds/2146842.cms",
             "Kite MCP (Zerodha, read-only)": "https://mcp.kite.trade/mcp"}
    ok = True
    for name, url in hosts.items():
        try:
            code = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15).status_code
            good = code < 500 and code != 403 or "kite" in url and code in (401, 403, 405, 406)
            print(f"  {'OK  ' if good else 'FAIL'} {name}  (HTTP {code})")
        except Exception as e:  # noqa: BLE001
            good = False
            print(f"  FAIL {name}  ({type(e).__name__})")
        ok &= good
    print("All sources reachable." if ok else "Some sources are blocked. Run this on your own computer or allow these sites.")
    return ok


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="all",
                    choices=["all", "backtest", "scout", "review", "buy", "cashout", "trades", "holdings", "dashboard", "doctor", "test"])
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--confirm", action="store_true")
    ap.add_argument("--years", type=int, default=None)
    ap.add_argument("--csv", type=Path, default=None)
    args = ap.parse_args()

    cfg = Config()
    if args.demo:
        cfg = replace(cfg, data_dir=DATA / "demo")
        print("DEMO MODE: made-up companies. Good for seeing how it works. Results mean nothing.")
    cfg.data_dir.mkdir(parents=True, exist_ok=True)

    if args.command == "test":
        sys.exit(subprocess.call([sys.executable, "-m", "pytest", "-q", str(ROOT / "tests")]))
    if args.command == "doctor":
        sys.exit(0 if doctor() else 1)
    if args.command == "holdings":
        from stockguy import kite_readonly
        p = kite_readonly.import_csv(args.csv, cfg.data_dir) if args.csv else kite_readonly.sync_from_kite(cfg.data_dir)
        print(f"Saved your holdings (read-only copy) to {p}")
        return

    from stockguy import dashboard
    provider = app.make_provider(cfg, demo=args.demo, offline=args.offline)
    if args.command == "dashboard":
        print("Dashboard:", dashboard.build(provider, cfg))
        return
    live = not args.demo and not args.offline       # paper trades at the real price, at the real time
    if args.command == "trades":
        app.cmd_trades(provider, cfg)
        return
    if not args.demo:
        app.preload(provider)
    if args.command == "backtest":
        app.cmd_backtest(provider, cfg, args.years)
    elif args.command == "scout":
        app.cmd_scout(provider, cfg, live_events=not args.demo and not args.offline)
    elif args.command == "review":
        app.cmd_review(provider, cfg, force=args.force, live=live)
    elif args.command == "buy":
        A = app.cmd_scout(provider, cfg, live_events=live)
        app.cmd_review(provider, cfg, A=A, force=True, live=live)
        app.cmd_trades(provider, cfg)
    elif args.command == "cashout":
        app.cmd_cashout(provider, cfg, confirm=args.confirm, live=live)
    else:
        rep = app._load(cfg.data_dir / "backtest_report.json", None)
        if not rep or args.force:
            app.cmd_backtest(provider, cfg, args.years)        # tests first
        else:
            app.print_report(rep)
        A = app.cmd_scout(provider, cfg, live_events=not args.demo and not args.offline)
        app.cmd_review(provider, cfg, A=A, force=args.force, live=live)
    print("Trade log: data/trades.md  (or: python run.py trades)")
    print("\nDashboard:", dashboard.build(provider, cfg))
    print("PAPER MODE. No real order was placed, and none can be.")


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as e:      # data source problems: say it plainly, no stack trace
        sys.exit(f"\nSTOPPED: {e}")
