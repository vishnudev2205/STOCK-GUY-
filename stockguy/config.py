"""Every rule and threshold lives here, in one place, so you can read and change them."""
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"


@dataclass
class Config:
    # ---- money -----------------------------------------------------------
    capital: float = 50_000.0             # Rs 50,000 virtual
    max_trade_value: float = 15_000.0     # never put more than this into a single buy
    cash_buffer: float = 0.02             # keep 2% in cash for charges and tax

    # ---- no look-ahead: when is a result "known"? ------------------------
    annual_lag_days: int = 90             # annual results assumed public 90 days after year end
    quarterly_lag_days: int = 60          # quarterly results assumed public 60 days after quarter end
    stale_days: int = 45                  # no price for this long = treated as suspended/delisted

    # ---- Bot 1: Scout ----------------------------------------------------
    q_rev_yoy: float = 0.15               # strong quarter: sales up 15% YoY ...
    q_pat_yoy: float = 0.20               # ... and profit up 20% YoY
    debt_cut: float = 0.20                # debt reduced by 20% in a year
    trigger_lookback_days: int = 200      # a trigger stays "fresh" this long

    # ---- Bot 2: Business Analyst ----------------------------------------
    min_years: int = 3                    # refuse to judge on less than this
    ideal_years: int = 5                  # flagged as "short history" below this
    max_years: int = 10
    roce_min: float = 0.15
    roe_min_fin: float = 0.15             # banks/NBFCs are judged on ROE instead of ROCE
    de_max: float = 0.5
    cfo_pat_min: float = 0.70             # cash profits vs reported profits
    rev_cagr_min: float = 0.10
    pat_cagr_min: float = 0.12
    promoter_min: float = 0.35
    pledge_max: float = 0.10

    # ---- Bot 3: Framework Judge -----------------------------------------
    framework_pass_score: int = 60        # out of 100
    min_frameworks: int = 2

    # ---- Bot 4: Valuation Analyst ---------------------------------------
    pe_vs_own_history_max: float = 1.25   # reject if PE > 1.25x its own median
    peg_max: float = 2.0
    pe_vs_peers_max: float = 1.5
    pe_history_years: int = 5

    # ---- Bot 5: Risk Manager --------------------------------------------
    max_stock_w: float = 0.10
    max_sector_w: float = 0.25
    min_stocks: int = 15
    max_stocks: int = 20
    trim_tolerance: float = 0.02          # trim back to 10% once a stock passes 12%
    skip_if_held_real: bool = False       # True = never paper-buy what you already own in Zerodha

    # ---- Bot 6: Reviewer -------------------------------------------------
    review_months: tuple = (2, 5, 8, 11)  # last trading day of Feb/May/Aug/Nov (after results season)
    max_hold_years: float = 5.0
    thesis_roce_floor: float = 0.12
    thesis_de_ceiling: float = 1.0
    extreme_pe_mult: float = 2.0
    extreme_peg: float = 3.0

    # ---- costs (Zerodha, equity delivery) --------------------------------
    brokerage: float = 0.0                # Zerodha delivery brokerage is zero
    stt: float = 0.001                    # 0.1% on buy and on sell
    exch_txn: float = 0.0000345           # NSE transaction charge (upper end of the last decade)
    sebi: float = 0.000001
    stamp_buy: float = 0.00015
    gst: float = 0.18                     # on brokerage + exchange + SEBI charges
    dp_charge: float = 15.34              # per stock per sell day
    slippage: float = 0.001               # 0.1% worse price than the close, each way
    cess: float = 0.04
    delist_haircut: float = 0.5           # held stock stops trading: assume half the last price is lost

    # ---- backtest ---------------------------------------------------------
    backtest_years: int = 10
    benchmark_symbol: str = "NIFTYBEES"   # a real Nifty 50 index ETF (has real costs inside its NAV)
    benchmark_fallback: str = "^NSEI"

    data_dir: Path = field(default_factory=lambda: DATA)


FINANCIAL_WORDS = ("financ", "bank", "insurance", "nbfc")
CONSUMER_WORDS = ("consumer", "fmcg", "fast moving", "retail", "durables", "textile", "apparel",
                  "beverage", "food", "tobacco", "household", "personal")
# Jhunjhunwala-style "India growth" themes: credit, consumption, building the country, health.
INDIA_THEME_WORDS = FINANCIAL_WORDS + CONSUMER_WORDS + (
    "capital goods", "industrial", "construction", "infrastructure", "cement", "auto", "healthcare",
    "pharma", "realty", "real estate", "power", "telecom", "communication", "services")
# Kedia-style "extra-large market potential": sectors still small relative to where India is going.
BIG_RUNWAY_WORDS = CONSUMER_WORDS + (
    "capital goods", "industrial", "healthcare", "pharma", "chemical", "technology", "information",
    "services", "auto", "construction", "realty", "telecom", "financ")


def sector_has(sector: str, words) -> bool:
    s = (sector or "").lower()
    return any(w in s for w in words)
