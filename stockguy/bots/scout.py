"""Bot 1: Scout. Runs weekly. Finds reasons to look at a company *now*.

Triggers it can compute from numbers (used in both the backtest and live):
  - strong quarterly results      - debt reduction
Triggers it reads from exchange filings and news (live only, because free
historical archives of these do not exist):
  - new CEO/MD   - merger or acquisition   - large orders
  - new capacity - promoter buying
The Scout cannot tell whether a new CEO is "experienced". It flags the
appointment; reading the person's background is your job.
"""
from __future__ import annotations

import pandas as pd

from ..config import Config

KEYWORDS = {
    "New CEO/MD": [("appoint", "chief executive"), ("appoint", "ceo"), ("appoint", "managing director")],
    "Merger/acquisition": [("acquisition",), ("acquire",), ("merger",), ("amalgamation",), ("scheme of arrangement",)],
    "Large order": [("bagging",), ("receipt of order",), ("order win",), ("awarding of order",),
                    ("letter of award",), ("work order",), ("bags order",), ("wins order",)],
    "New capacity": [("capacity expansion",), ("commencement of commercial production",), ("commissioning",),
                     ("new plant",), ("capacity addition",), ("greenfield",), ("brownfield",)],
}


def classify(text: str) -> list[str]:
    t = (text or "").lower()
    return [kind for kind, combos in KEYWORDS.items() if any(all(w in t for w in c) for c in combos)]


def _yoy(now, then):
    if then is None or pd.isna(then) or pd.isna(now) or then <= 0:
        return None
    return now / then - 1


def number_triggers(view, cfg: Config) -> list[dict]:
    """Triggers from published financial results only. Point-in-time safe."""
    out = []
    fresh_after = view.asof - pd.Timedelta(days=cfg.trigger_lookback_days)
    q = view.quarterly
    done = False
    if len(q) >= 5:
        last = q.iloc[-1]
        if last["available"] >= fresh_after:
            target = q.index[-1] - pd.DateOffset(years=1)
            prev = q[(q.index >= target - pd.Timedelta(days=20)) & (q.index <= target + pd.Timedelta(days=20))]
            if len(prev):
                rg, pg = _yoy(last["revenue"], prev["revenue"].iloc[-1]), _yoy(last["net_income"], prev["net_income"].iloc[-1])
                done = True
                if rg is not None and pg is not None and rg >= cfg.q_rev_yoy and pg >= cfg.q_pat_yoy:
                    out.append({"type": "Strong quarterly results", "date": str(last["available"].date()),
                                "detail": f"Quarter ended {q.index[-1]:%b %Y}: sales +{rg:.0%}, profit +{pg:.0%} vs a year ago"})
    a = view.annual
    if len(a) >= 2 and a["available"].iloc[-1] >= fresh_after:
        last, prev = a.iloc[-1], a.iloc[-2]
        if not done:   # no quarterly history for that date: fall back to the annual result
            rg, pg = _yoy(last["revenue"], prev["revenue"]), _yoy(last["net_income"], prev["net_income"])
            if rg is not None and pg is not None and rg >= cfg.q_rev_yoy and pg >= cfg.q_pat_yoy:
                out.append({"type": "Strong results", "date": str(last["available"].date()),
                            "detail": f"Year ended {a.index[-1]:%b %Y}: sales +{rg:.0%}, profit +{pg:.0%}"})
        if not view.is_financial and pd.notna(prev["total_debt"]) and prev["total_debt"] > 0 and pd.notna(last["total_debt"]):
            cut = 1 - last["total_debt"] / prev["total_debt"]
            material = pd.isna(prev["equity"]) or prev["total_debt"] > 0.1 * prev["equity"]
            if cut >= cfg.debt_cut and material:
                out.append({"type": "Debt reduction", "date": str(last["available"].date()),
                            "detail": f"Debt cut by {cut:.0%} in the year ended {a.index[-1]:%b %Y}"})
    return out


# ---- live only: exchange filings and news -----------------------------------
def fetch_events(symbols_to_names: dict, days: int = 8) -> tuple[dict, list[str]]:
    """Returns ({symbol: [trigger, ...]}, problems). Never raises: a dead feed is just reported."""
    import requests
    events: dict[str, list] = {}
    problems = []
    end, start = pd.Timestamp.today(), pd.Timestamp.today() - pd.Timedelta(days=days)
    rng = f"from_date={start:%d-%m-%Y}&to_date={end:%d-%m-%Y}"
    ua = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/126 Safari/537.36",
          "Accept": "application/json,text/plain,*/*", "Referer": "https://www.nseindia.com/"}

    def add(sym, kind, detail, date):
        if sym in symbols_to_names:
            events.setdefault(sym, []).append({"type": kind, "detail": detail[:220], "date": str(date)[:10]})

    try:
        s = requests.Session()
        s.headers.update(ua)
        s.get("https://www.nseindia.com/", timeout=20)          # NSE needs a cookie first
        rows = s.get(f"https://www.nseindia.com/api/corporate-announcements?index=equities&{rng}", timeout=30).json()
        for r in rows if isinstance(rows, list) else []:
            text = f"{r.get('desc', '')} {r.get('attchmntText', '')}"
            for kind in classify(text):
                add(r.get("symbol"), kind, text.strip(), r.get("an_dt", ""))
        pit = s.get(f"https://www.nseindia.com/api/corporates-pit?index=equities&{rng}", timeout=30).json()
        for r in (pit.get("data", []) if isinstance(pit, dict) else []):
            who, mode, kind = str(r.get("personCategory", "")), str(r.get("acqMode", "")), str(r.get("tdpTransactionType", ""))
            if "promoter" in who.lower() and "market purchase" in mode.lower() and kind.lower() == "buy":
                add(r.get("symbol"), "Promoter buying",
                    f"{r.get('acqName', 'Promoter')} bought shares worth Rs {r.get('secVal', '?')} in the market",
                    r.get("date", ""))
    except Exception as e:  # noqa: BLE001
        problems.append(f"NSE filings feed not reachable ({type(e).__name__}). Number-based triggers still work.")

    try:
        import feedparser
        feeds = ["https://economictimes.indiatimes.com/markets/stocks/rssfeeds/2146842.cms",
                 "https://www.moneycontrol.com/rss/business.xml"]
        short = {sym: n.lower().replace(" limited", "").replace(" ltd.", "").replace(" ltd", "").strip()
                 for sym, n in symbols_to_names.items()}
        for f in feeds:
            for e in feedparser.parse(f).entries[:150]:
                title = e.get("title", "")
                kinds = classify(title + " " + e.get("summary", ""))
                if not kinds:
                    continue
                tl = title.lower()
                for sym, nm in short.items():
                    if len(nm) > 5 and nm in tl:
                        for k in kinds:
                            add(sym, k, "News: " + title, e.get("published", "")[:16])
    except Exception as e:  # noqa: BLE001
        problems.append(f"News RSS not reachable ({type(e).__name__}).")
    return events, problems


def run(view, cfg: Config, external: list | None = None) -> list[dict]:
    return number_triggers(view, cfg) + list(external or [])
