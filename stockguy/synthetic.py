"""A made-up market for testing the machinery. NOT REAL COMPANIES. NOT REAL RESULTS.

It exists so the rules can be exercised without the internet: companies that
grow, companies that stagnate, companies that go bust and get delisted, an
index whose membership changes. Numbers from this say nothing about real stocks.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .config import Config
from .data import MarketData, StockData

SECTORS = ["Financial Services", "Fast Moving Consumer Goods", "Consumer Durables", "Capital Goods",
           "Healthcare", "Information Technology", "Chemicals", "Automobile And Auto Components",
           "Metals & Mining", "Power"]
KINDS = {  # growth, margin, roce, debt share, cash conversion, typical PE
    "compounder": (0.20, 0.14, 0.26, 0.10, 1.00, 28),
    "average": (0.10, 0.08, 0.15, 0.30, 0.85, 18),
    "weak": (0.03, 0.04, 0.08, 0.55, 0.50, 11),
}


class SyntheticData(MarketData):
    label = "SYNTHETIC TEST DATA (made-up companies)"
    point_in_time_universe = True

    def __init__(self, cfg: Config, seed: int = 7, n: int = 90, years: int = 14, end: str = "2025-12-31",
                 index_size: int = 60):
        super().__init__(cfg)
        rng = np.random.default_rng(seed)
        self.days = pd.bdate_range(end=end, periods=years * 261)
        mkt = np.cumsum(rng.normal(0.0002, 0.009, len(self.days)))
        self.delisted = []
        for i in range(n):
            kind = rng.choice(list(KINDS), p=[0.25, 0.45, 0.30])
            self._stocks[f"SYN{i:03d}"] = self._make(f"SYN{i:03d}", kind, rng, mkt)
        # index membership, rebuilt twice a year from what was listed and biggest then
        self._members = {}
        for d in self.days[::130]:
            caps = {}
            for sym, s in self._stocks.items():
                px = s.close.loc[:d]
                if len(px) and (d - px.index[-1]).days < 10:
                    caps[sym] = px.iloc[-1] * s.annual["shares"].iloc[0]
            top = sorted(caps, key=caps.get, reverse=True)[:index_size]
            self._members[d] = pd.DataFrame({"symbol": top, "name": [self._stocks[t].name for t in top],
                                             "sector": [self._stocks[t].sector for t in top]})
        # index fund: equal-weight daily return of current members (a dead stock stays at its last price)
        rets = pd.DataFrame({sym: s.tr.reindex(self.days).ffill().pct_change() for sym, s in self._stocks.items()})
        idx_ret = pd.Series(0.0, index=self.days)
        keys = sorted(self._members)
        for j, d in enumerate(keys):
            end_d = keys[j + 1] if j + 1 < len(keys) else self.days[-1] + pd.Timedelta(days=1)
            seg = rets.loc[(rets.index > d) & (rets.index <= end_d), list(self._members[d]["symbol"])]
            idx_ret.loc[seg.index] = seg.mean(axis=1).fillna(0.0)
        self._bench = 100 * (1 + idx_ret).cumprod()

    def _make(self, sym, kind, rng, mkt) -> StockData:
        g, margin, roce, debt, cashc, pe0 = KINDS[kind]
        days = self.days
        fy_ends = pd.date_range(days[0] - pd.DateOffset(years=6), days[-1], freq="YE-MAR")
        fail_at = rng.integers(8, len(fy_ends) - 1) if rng.random() < 0.12 else None
        rev, rows = rng.uniform(300, 4000) * 1e7, []   # rupees
        shares = float(rng.integers(5, 60)) * 2e7
        for k, fy in enumerate(fy_ends):
            failed = fail_at is not None and k >= fail_at
            rev *= 1 + (rng.normal(-0.35, 0.1) if failed else rng.normal(g, 0.08))
            mg = -0.15 if failed else max(0.005, rng.normal(margin, margin * 0.2))
            ni = rev * mg
            ebit = ni * 1.45 if ni > 0 else ni
            cap = max(abs(ebit), 1e6) / max(0.03, rng.normal(roce, roce * 0.12))
            d_share = min(0.9, debt + (0.3 if failed else 0))
            rows.append({"revenue": rev, "net_income": ni, "ebit": ebit, "total_assets": cap * 1.3,
                         "current_liabilities": cap * 0.3, "total_debt": cap * d_share, "equity": cap * (1 - d_share),
                         "cfo": ni * rng.normal(cashc, 0.15), "capex": -rev * 0.05, "shares": shares})
        annual = pd.DataFrame(rows, index=fy_ends)
        annual["available"] = annual.index + pd.Timedelta(days=self.cfg.annual_lag_days)
        annual.index.name = "period_end"
        q_rows, q_idx = [], []
        for fy, r in annual.iterrows():
            w = rng.dirichlet(np.ones(4) * 40)
            for j in range(4):
                q_idx.append(fy - pd.DateOffset(months=3 * (3 - j)) + pd.offsets.MonthEnd(0))
                q_rows.append({"revenue": r["revenue"] * w[j], "net_income": r["net_income"] * w[j]})
        quarterly = pd.DataFrame(q_rows, index=pd.DatetimeIndex(q_idx, name="period_end"))
        quarterly["available"] = quarterly.index + pd.Timedelta(days=self.cfg.quarterly_lag_days)

        eps = pd.Series((annual["net_income"] / shares).values, index=annual["available"]).reindex(days, method="ffill")
        eps = eps.where(eps > 0, eps.abs() * 0.15).bfill()
        mood = np.zeros(len(days))
        shocks = rng.normal(0, 0.017, len(days))
        for t in range(1, len(days)):
            mood[t] = 0.997 * mood[t - 1] + shocks[t]
        close = pd.Series(pe0 * eps.values * np.exp(mood + 0.5 * (mkt - mkt.mean())), index=days)
        start = days[int(rng.integers(0, len(days) // 3))] if rng.random() < 0.15 else days[0]
        close = close.loc[start:]
        if fail_at is not None:   # stops trading about nine months after the collapse becomes public
            stop = annual["available"].iloc[fail_at] + pd.Timedelta(days=270)
            if stop < days[-1]:
                close = close.loc[:stop]
                self.delisted.append(sym)
        sector = SECTORS[int(rng.integers(0, len(SECTORS)))]
        return StockData(symbol=sym, name=f"Synthetic {kind.title()} {sym[3:]}", sector=sector, close=close,
                         tr=close * 1.0, annual=annual, quarterly=quarterly)

    def universe(self, asof):
        prior = [d for d in sorted(self._members) if d <= pd.Timestamp(asof)]
        return self._members[prior[-1] if prior else sorted(self._members)[0]]

    def universe_date(self, asof):
        prior = [d for d in sorted(self._members) if d <= pd.Timestamp(asof)]
        return prior[-1] if prior else None

    def all_symbols(self):
        return sorted(self._stocks)

    def stock(self, symbol):
        return self._stocks.get(symbol)

    def benchmark(self):
        return self._bench
