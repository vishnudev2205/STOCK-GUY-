"""Data layer.

The one rule that matters here: bots never touch raw data. They only get a
`StockView`, which is a company's data *cut off at a date*. A price after that
date, or a result not yet published on that date, is simply not in the view.
That is how "no look-ahead" is enforced for both the backtest and live runs.

Sources (all free):
  - Yahoo Finance via yfinance (.NS tickers): prices, splits, financial statements
  - NSE index CSV + Wayback Machine snapshots of it: Nifty 500 members on past dates
  - Optional: your own CSVs in data/fundamentals_csv/ to extend history
"""
from __future__ import annotations

import io
import json
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from .config import Config, sector_has, FINANCIAL_WORDS

ANNUAL_COLS = ["revenue", "net_income", "ebit", "total_assets", "current_liabilities",
               "total_debt", "equity", "cfo", "capex", "shares"]
QUARTERLY_COLS = ["revenue", "net_income"]


def empty_frame(cols):
    df = pd.DataFrame(columns=cols + ["available"], index=pd.DatetimeIndex([], name="period_end"))
    df["available"] = pd.to_datetime(df["available"])
    return df


@dataclass
class StockData:
    """Everything we know about one company, across all time. Bots must not use this directly."""
    symbol: str
    name: str = ""
    sector: str = "Unknown"
    close: pd.Series = field(default_factory=lambda: pd.Series(dtype=float))   # split-adjusted close
    tr: pd.Series = field(default_factory=lambda: pd.Series(dtype=float))      # close incl. dividends
    splits: pd.Series = field(default_factory=lambda: pd.Series(dtype=float))
    annual: pd.DataFrame = field(default_factory=lambda: empty_frame(ANNUAL_COLS))
    quarterly: pd.DataFrame = field(default_factory=lambda: empty_frame(QUARTERLY_COLS))
    promoter_pct: float | None = None    # latest only; never used in the backtest
    pledge_pct: float | None = None      # latest only; never used in the backtest
    _pe: pd.Series | None = None

    def split_factor_after(self, date) -> float:
        """Yahoo prices are adjusted for every later split/bonus. Share counts in old
        balance sheets are not. This factor puts both on the same footing."""
        if self.splits is None or len(self.splits) == 0:
            return 1.0
        later = self.splits[self.splits.index > date]
        later = later[later > 0]
        return float(later.prod()) if len(later) else 1.0

    def eps_table(self) -> pd.DataFrame:
        a = self.annual.dropna(subset=["net_income", "shares"])
        a = a[a["shares"] > 0]
        if a.empty:
            return pd.DataFrame(columns=["available", "eps"])
        adj_shares = [sh * self.split_factor_after(d) for d, sh in a["shares"].items()]
        return pd.DataFrame({"available": a["available"].values,
                             "eps": a["net_income"].values / np.array(adj_shares)})

    def pe_series(self) -> pd.Series:
        """Month-end PE. Each point uses only the annual profit already published by then."""
        if self._pe is None:
            eps = self.eps_table()
            if eps.empty or self.close.empty:
                self._pe = pd.Series(dtype=float)
            else:
                monthly = self.close.resample("ME").last().dropna()
                e = pd.Series(eps["eps"].values, index=pd.DatetimeIndex(eps["available"])).sort_index()
                e = e[~e.index.duplicated(keep="last")]
                e_m = e.reindex(monthly.index, method="ffill")
                pe = monthly / e_m
                self._pe = pe[(e_m > 0)].dropna()
        return self._pe


class StockView:
    """A company as it looked on `asof`. Nothing later than `asof` is reachable from here."""

    def __init__(self, s: StockData, asof: pd.Timestamp, cfg: Config):
        self.symbol, self.name, self.sector = s.symbol, s.name or s.symbol, s.sector
        self.asof = pd.Timestamp(asof)
        self.close = s.close.loc[:self.asof]
        self.tr = s.tr.loc[:self.asof]
        self.annual = s.annual[s.annual["available"] <= self.asof].sort_index().tail(cfg.max_years)
        self.quarterly = s.quarterly[s.quarterly["available"] <= self.asof].sort_index()
        self.pe_history = s.pe_series().loc[:self.asof].tail(cfg.pe_history_years * 12)
        self.is_financial = sector_has(self.sector, FINANCIAL_WORDS)
        self.promoter_pct, self.pledge_pct = s.promoter_pct, s.pledge_pct
        self._s, self._cfg = s, cfg

    @property
    def has_price(self) -> bool:
        return len(self.close) > 0

    @property
    def price(self) -> float:
        return float(self.close.iloc[-1]) if self.has_price else float("nan")

    @property
    def trade_price(self) -> float:
        return float(self.tr.iloc[-1]) if len(self.tr) else float("nan")

    @property
    def stale(self) -> bool:
        if not self.has_price:
            return True
        return (self.asof - self.close.index[-1]).days > self._cfg.stale_days

    @property
    def listed_years(self) -> float:
        return (self.asof - self.close.index[0]).days / 365.25 if self.has_price else 0.0

    @property
    def eps(self) -> float | None:
        a = self.annual.dropna(subset=["net_income", "shares"])
        if a.empty or a["shares"].iloc[-1] <= 0:
            return None
        d = a.index[-1]
        return float(a["net_income"].iloc[-1] / (a["shares"].iloc[-1] * self._s.split_factor_after(d)))

    @property
    def mcap(self) -> float | None:
        a = self.annual.dropna(subset=["shares"])
        if a.empty or not self.has_price:
            return None
        d = a.index[-1]
        return float(self.price * a["shares"].iloc[-1] * self._s.split_factor_after(d))

    @property
    def pe(self) -> float | None:
        e = self.eps
        if e is None or e <= 0 or not self.has_price:
            return None
        return self.price / e


# ---------------------------------------------------------------------------
class MarketData:
    """Base class. `universe(asof)` must return the index members *as of that date*."""
    point_in_time_universe = False
    label = "base"
    notes: list[str]

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.notes = []
        self._stocks: dict[str, StockData | None] = {}

    def universe(self, asof) -> pd.DataFrame:      # columns: symbol, name, sector
        raise NotImplementedError

    def all_symbols(self) -> list[str]:
        raise NotImplementedError

    def stock(self, symbol) -> StockData | None:
        raise NotImplementedError

    def benchmark(self) -> pd.Series:              # total-return price series of the index fund
        raise NotImplementedError

    def view(self, symbol, asof) -> StockView | None:
        s = self.stock(symbol)
        return StockView(s, pd.Timestamp(asof), self.cfg) if s is not None else None

    def calendar(self) -> pd.DatetimeIndex:
        return self.benchmark().index


# ---------------------------------------------------------------------------
YF_ROWS = {
    "revenue": ["Total Revenue", "Operating Revenue"],
    "net_income": ["Net Income Common Stockholders", "Net Income"],
    "ebit": ["EBIT", "Operating Income"],
    "total_assets": ["Total Assets"],
    "current_liabilities": ["Current Liabilities", "Total Current Liabilities"],
    "total_debt": ["Total Debt"],
    "equity": ["Stockholders Equity", "Common Stock Equity", "Total Equity Gross Minority Interest"],
    "cfo": ["Operating Cash Flow", "Cash Flow From Continuing Operating Activities"],
    "capex": ["Capital Expenditure"],
    "shares": ["Ordinary Shares Number", "Share Issued"],
}

NIFTY500_URLS = [
    "https://nsearchives.nseindia.com/content/indices/ind_nifty500list.csv",
    "https://archives.nseindia.com/content/indices/ind_nifty500list.csv",
    "https://www.niftyindices.com/IndexConstituent/ind_nifty500list.csv",
]
# The same file has lived at several addresses over the years; the Wayback Machine kept copies.
NIFTY500_OLD_URLS = NIFTY500_URLS + [
    "https://www1.nseindia.com/content/indices/ind_nifty500list.csv",
    "https://www.nseindia.com/content/indices/ind_nifty500list.csv",
]
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/126 Safari/537.36"}


def _pick_rows(df: pd.DataFrame, mapping: dict) -> pd.DataFrame:
    """yfinance statements: rows are line items, columns are period ends. Flip and rename."""
    out = {}
    if df is None or getattr(df, "empty", True):
        return pd.DataFrame()
    for col, names in mapping.items():
        for n in names:
            if n in df.index:
                out[col] = pd.to_numeric(df.loc[n], errors="coerce")
                break
    res = pd.DataFrame(out)
    res.index = pd.to_datetime(res.index).tz_localize(None)
    return res.sort_index()


def parse_index_csv(text: str) -> pd.DataFrame:
    df = pd.read_csv(io.StringIO(text))
    df.columns = [c.strip().lower() for c in df.columns]
    if "symbol" not in df.columns:
        raise ValueError("not an NSE index list")
    return pd.DataFrame({
        "symbol": df["symbol"].astype(str).str.strip(),
        "name": df.get("company name", df["symbol"]).astype(str).str.strip(),
        "sector": df.get("industry", pd.Series("Unknown", index=df.index)).astype(str).str.strip().str.title(),
    })


class YFinanceData(MarketData):
    """Real data. Everything is cached under data/cache so a re-run is fast and resumable."""
    label = "Yahoo Finance + NSE"

    def __init__(self, cfg: Config, refresh_days: int = 6, offline: bool = False):
        super().__init__(cfg)
        self.cache = cfg.data_dir / "cache"
        (self.cache / "stocks").mkdir(parents=True, exist_ok=True)
        (self.cache / "universe").mkdir(parents=True, exist_ok=True)
        self.refresh_days, self.offline = refresh_days, offline
        self._snapshots: dict[pd.Timestamp, pd.DataFrame] | None = None
        self._bench = None
        self.missing: set[str] = set()

    # ---- universe ---------------------------------------------------------
    def _get(self, url, timeout=30):
        import requests
        r = requests.get(url, headers=UA, timeout=timeout)
        r.raise_for_status()
        return r

    def _load_snapshots(self):
        if self._snapshots is not None:
            return
        udir = self.cache / "universe"
        today = pd.Timestamp.today().normalize()
        cur = udir / f"{today:%Y%m%d}.csv"
        have_recent = any((today - pd.Timestamp(p.stem)).days <= self.refresh_days for p in udir.glob("2*.csv"))
        if not self.offline and not have_recent:
            for url in NIFTY500_URLS:
                try:
                    parse_index_csv(self._get(url).text).to_csv(cur, index=False)
                    break
                except Exception as e:  # noqa: BLE001
                    self.notes.append(f"Current Nifty 500 list failed from {url}: {e}")
        if not self.offline and not (udir / "wayback.done").exists():
            self._fetch_wayback(udir)
        self._snapshots = {}
        for p in sorted(udir.glob("2*.csv")):
            try:
                self._snapshots[pd.Timestamp(p.stem)] = pd.read_csv(p)
            except Exception:  # noqa: BLE001
                pass
        if not self._snapshots:
            raise RuntimeError("Could not load the Nifty 500 list (no network and nothing cached). "
                               "Run `python run.py doctor` to see which sources are reachable.")
        self.point_in_time_universe = len(self._snapshots) >= 4

    def _fetch_wayback(self, udir: Path):
        """Two snapshots a year of the official list, as it actually was back then."""
        got, seen = 0, set()
        for url in NIFTY500_OLD_URLS:
            try:
                cdx = self._get("https://web.archive.org/cdx/search/cdx?output=json&filter=statuscode:200"
                                f"&collapse=timestamp:6&url={url.split('://')[1]}", timeout=60).json()
            except Exception as e:  # noqa: BLE001
                self.notes.append(f"Wayback index lookup failed for {url}: {e}")
                continue
            for row in cdx[1:]:
                ts, original = row[1], row[2]
                half = (ts[:4], "H1" if ts[4:6] <= "06" else "H2")
                if half in seen:
                    continue
                try:
                    txt = self._get(f"https://web.archive.org/web/{ts}id_/{original}", timeout=60).text
                    df = parse_index_csv(txt)
                    if len(df) < 400:
                        continue
                    df.to_csv(udir / f"{ts[:8]}.csv", index=False)
                    seen.add(half)
                    got += 1
                    time.sleep(1)
                except Exception:  # noqa: BLE001
                    continue
        if got:
            (udir / "wayback.done").write_text(str(got))
        else:
            self.notes.append("No historical Nifty 500 lists could be fetched from the Wayback Machine.")

    def universe(self, asof) -> pd.DataFrame:
        self._load_snapshots()
        asof = pd.Timestamp(asof)
        dates = sorted(self._snapshots)
        prior = [d for d in dates if d <= asof]
        # If there is no list old enough we must use the earliest one we have. That is a
        # survivorship leak, and the backtest reports how many dates were affected.
        return self._snapshots[prior[-1] if prior else dates[0]]

    def universe_date(self, asof):
        self._load_snapshots()
        prior = [d for d in sorted(self._snapshots) if d <= pd.Timestamp(asof)]
        return prior[-1] if prior else None

    def all_symbols(self) -> list[str]:
        self._load_snapshots()
        return sorted({s for df in self._snapshots.values() for s in df["symbol"]})

    # ---- one stock --------------------------------------------------------
    def _sector_of(self, symbol):
        self._load_snapshots()
        for d in sorted(self._snapshots, reverse=True):
            df = self._snapshots[d]
            hit = df[df["symbol"] == symbol]
            if len(hit):
                return hit["name"].iloc[0], hit["sector"].iloc[0]
        return symbol, "Unknown"

    def _download(self, symbol: str, sdir: Path):
        import yfinance as yf
        ticker = symbol if symbol.startswith("^") else f"{symbol}.NS"
        t = yf.Ticker(ticker)
        h = t.history(period="max", auto_adjust=False, actions=True)
        if h is None or h.empty:
            (sdir / "missing").write_text(pd.Timestamp.today().isoformat())
            return
        h.index = pd.to_datetime(h.index).tz_localize(None).normalize()
        h = h[~h.index.duplicated(keep="last")]
        cols = [c for c in ["Close", "Adj Close", "Stock Splits"] if c in h.columns]
        h[cols].to_csv(sdir / "prices.csv")
        if symbol.startswith("^"):
            return
        try:
            a = _pick_rows(t.income_stmt, {k: YF_ROWS[k] for k in ("revenue", "net_income", "ebit")})
            b = _pick_rows(t.balance_sheet, {k: YF_ROWS[k] for k in
                                             ("total_assets", "current_liabilities", "total_debt", "equity", "shares")})
            c = _pick_rows(t.cashflow, {k: YF_ROWS[k] for k in ("cfo", "capex")})
            pd.concat([a, b, c], axis=1).to_csv(sdir / "annual.csv")
            q = _pick_rows(t.quarterly_income_stmt, {k: YF_ROWS[k] for k in ("revenue", "net_income")})
            q.to_csv(sdir / "quarterly.csv")
        except Exception as e:  # noqa: BLE001
            self.notes.append(f"{symbol}: financial statements failed ({e})")
        try:
            info = t.info or {}
            (sdir / "meta.json").write_text(json.dumps({
                "name": info.get("longName"), "yahoo_sector": info.get("sector"),
                "insiders_pct": info.get("heldPercentInsiders")}))
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.4)

    def stock(self, symbol) -> StockData | None:
        if symbol in self._stocks:
            return self._stocks[symbol]
        sdir = self.cache / "stocks" / symbol.replace("^", "_idx_")
        sdir.mkdir(exist_ok=True)
        px = sdir / "prices.csv"
        marker = px if px.exists() else sdir / "missing"
        fresh = marker.exists() and (time.time() - marker.stat().st_mtime) < self.refresh_days * 86400
        if not fresh and not self.offline:
            for attempt in range(3):
                try:
                    self._download(symbol, sdir)
                    break
                except Exception as e:  # noqa: BLE001
                    if attempt == 2:
                        self.notes.append(f"{symbol}: download failed ({e})")
                    time.sleep(3 * (attempt + 1))
        s = self._read(symbol, sdir)
        if s is None:
            self.missing.add(symbol)
        self._stocks[symbol] = s
        return s

    def _read(self, symbol, sdir: Path) -> StockData | None:
        px = sdir / "prices.csv"
        if not px.exists():
            return None
        h = pd.read_csv(px, index_col=0, parse_dates=True)
        h = h[h["Close"] > 0]
        if h.empty:
            return None
        name, sector = (symbol, "Index") if symbol.startswith("^") else self._sector_of(symbol)
        s = StockData(symbol=symbol, name=name, sector=sector, close=h["Close"].astype(float),
                      tr=h.get("Adj Close", h["Close"]).astype(float).ffill())
        if "Stock Splits" in h.columns:
            s.splits = h["Stock Splits"][h["Stock Splits"] > 0]
        s.annual = self._frame(sdir / "annual.csv", ANNUAL_COLS, self.cfg.annual_lag_days)
        s.quarterly = self._frame(sdir / "quarterly.csv", QUARTERLY_COLS, self.cfg.quarterly_lag_days)
        user = self.cfg.data_dir / "fundamentals_csv" / f"{symbol}.csv"
        if user.exists():   # your own longer history wins over Yahoo's
            u = self._frame(user, ANNUAL_COLS, self.cfg.annual_lag_days)
            s.annual = pd.concat([s.annual[~s.annual.index.isin(u.index)], u]).sort_index()
        meta = sdir / "meta.json"
        if meta.exists():
            m = json.loads(meta.read_text())
            s.name = s.name if s.name != symbol else (m.get("name") or symbol)
            if s.sector == "Unknown" and m.get("yahoo_sector"):
                s.sector = m["yahoo_sector"]
            s.promoter_pct = m.get("insiders_pct")
        return s

    @staticmethod
    def _frame(path: Path, cols, lag_days) -> pd.DataFrame:
        if not path.exists():
            return empty_frame(cols)
        try:
            df = pd.read_csv(path, index_col=0, parse_dates=True)
        except Exception:  # noqa: BLE001
            return empty_frame(cols)
        if df.empty:
            return empty_frame(cols)
        df.index = pd.to_datetime(df.index)
        for c in cols:
            if c not in df.columns:
                df[c] = np.nan
        if "available" in df.columns:
            df["available"] = pd.to_datetime(df["available"])
        else:
            df["available"] = df.index + pd.Timedelta(days=lag_days)
        df = df[cols + ["available"]].dropna(how="all", subset=cols).sort_index()
        df.index.name = "period_end"
        return df

    def benchmark(self) -> pd.Series:
        if self._bench is None:
            s = self.stock(self.cfg.benchmark_symbol)
            if s is None or len(s.tr) < 250:
                self.notes.append(f"{self.cfg.benchmark_symbol} unavailable; using {self.cfg.benchmark_fallback} "
                                  "(price index, dividends NOT included, which understates the index fund).")
                s = self.stock(self.cfg.benchmark_fallback)
            if s is None:
                raise RuntimeError("No benchmark data available.")
            self._bench = s.tr
        return self._bench
