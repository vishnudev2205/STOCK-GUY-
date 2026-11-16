"""The tests that must pass before any number from this project is believed."""
import copy
from dataclasses import replace
import inspect
import json

import numpy as np
import pandas as pd
import pytest

import stockguy
from stockguy import costs, kite_readonly, ledger as ledger_mod
from stockguy.backtest import run_backtest, xirr, index_fund
from stockguy.bots import risk_manager
from stockguy.config import Config
from stockguy.ledger import Ledger
from stockguy.pipeline import assess, quarterly_review
from stockguy.synthetic import SyntheticData

D = pd.Timestamp("2021-08-31")


def _decisions(A):
    return json.dumps([{k: a.get(k) for k in ("symbol", "stage", "composite", "triggers", "price")} for a in A],
                      sort_keys=True, default=str)


# ---- 1. no look-ahead ---------------------------------------------------------
def test_future_data_cannot_change_a_past_decision(cfg, market):
    """Scramble everything after D. Decisions made on D must be byte-for-byte identical."""
    before, _ = assess(market, D, cfg)
    twisted = SyntheticData(cfg, seed=7)
    rng = np.random.default_rng(1)
    for s in twisted._stocks.values():
        fut = s.close.index > D
        s.close[fut] = s.close[fut] * rng.uniform(0.1, 9)
        s.tr[fut] = s.tr[fut] * rng.uniform(0.1, 9)
        unpublished = s.annual["available"] > D
        s.annual.loc[unpublished, ["revenue", "net_income", "ebit", "cfo", "total_debt"]] *= rng.uniform(-3, 9)
        uq = s.quarterly["available"] > D
        s.quarterly.loc[uq, ["revenue", "net_income"]] *= rng.uniform(-3, 9)
        s._pe = None
    after, _ = assess(twisted, D, cfg)
    assert _decisions(before) == _decisions(after)


def test_results_are_invisible_until_published(cfg, market):
    s = market.stock("SYN001")
    fy_end = s.annual.index[-3]
    assert fy_end not in market.view("SYN001", fy_end + pd.Timedelta(days=30)).annual.index
    assert fy_end in market.view("SYN001", fy_end + pd.Timedelta(days=cfg.annual_lag_days)).annual.index


def test_view_never_shows_a_later_price(cfg, market):
    v = market.view("SYN001", D)
    assert v.close.index.max() <= D and v.tr.index.max() <= D and v.pe_history.index.max() <= D


def test_backtest_trades_do_not_depend_on_the_future(cfg, market):
    """A backtest that stops in 2021 must make the same trades whether or not 2022-25 exist."""
    full = run_backtest(market, cfg, years=4, end="2021-06-30", log=lambda *_: None)
    cut = SyntheticData(cfg, seed=7)
    for s in cut._stocks.values():
        s.close, s.tr = s.close.loc[:"2021-06-30"], s.tr.loc[:"2021-06-30"]
        s.annual = s.annual[s.annual["available"] <= "2021-06-30"]
        s.quarterly = s.quarterly[s.quarterly["available"] <= "2021-06-30"]
    cut._bench = cut._bench.loc[:"2021-06-30"]
    short = run_backtest(cut, cfg, years=4, end="2021-06-30", log=lambda *_: None)
    assert full["strategy"]["trades"] > 0
    assert full["closed"] == short["closed"]
    assert full["strategy"]["final_value"] == pytest.approx(short["strategy"]["final_value"])


# ---- 2. no survivorship bias ----------------------------------------------------
def test_failed_companies_are_in_the_past_universe(cfg, market):
    assert market.delisted, "the test market must contain failures"
    ever = set()
    for d in pd.date_range("2014-01-01", "2025-12-31", freq="6ME"):
        ever |= set(market.universe(d)["symbol"])
    dead_in_index = [s for s in market.delisted if s in ever]
    assert dead_in_index, "companies that later failed must have been pickable while they were alive"
    today = set(market.universe("2025-12-31")["symbol"])
    assert any(s not in today for s in dead_in_index)


def test_holding_that_stops_trading_is_written_off(cfg, market):
    cfg = replace(cfg, capital=1_000_000)
    sym = market.delisted[0]
    s = market.stock(sym)
    last_day = s.close.index[-1]
    buy_day = last_day - pd.Timedelta(days=400)
    led = Ledger(cfg)
    px = float(s.tr.loc[:buy_day].iloc[-1])
    led.buy(sym, int(50_000 / px), px, buy_day, s.name, s.sector, {"summary": "test"})
    review_day = last_day + pd.Timedelta(days=cfg.stale_days + 30)
    A, _ = assess(market, review_day, cfg, extra_symbols=[sym])
    journal = quarterly_review(led, A, {}, review_day, cfg)
    assert sym not in led.positions
    assert led.closed[-1]["sell_price"] == pytest.approx(float(s.tr.iloc[-1]) * (1 - cfg.delist_haircut) * (1 - cfg.slippage))
    assert any(j["action"] == "SELL" and "stopped trading" in j["text"] for j in journal)


# ---- 3. costs and tax -----------------------------------------------------------
def test_zerodha_delivery_charges(cfg):
    buy = costs.charges(100_000, "BUY", cfg)       # STT 100 + stamp 15 + (3.45 + 0.10) * 1.18
    sell = costs.charges(100_000, "SELL", cfg)     # STT 100 + DP 15.34 + (3.45 + 0.10) * 1.18
    assert buy == pytest.approx(100 + 15 + 3.55 * 1.18, abs=0.01)
    assert sell == pytest.approx(100 + 15.34 + 3.55 * 1.18, abs=0.01)


def test_every_trade_pays_charges_and_slippage(cfg):
    cfg = replace(cfg, capital=1_000_000)
    led = Ledger(cfg)
    led.buy("X", 100, 1000.0, "2023-01-02")
    assert led.cash < cfg.capital - 100 * 1000.0 - 100          # paid more than the sticker price
    led.sell("X", None, 1000.0, "2023-02-02")
    assert led.cash < cfg.capital - 400                          # a round trip at the same price loses money


@pytest.mark.parametrize("buy,sell,gain,expected", [
    ("2023-01-02", "2025-06-02", 225_000, (225_000 - 125_000) * 0.125 * 1.04),   # long-term, new rules
    ("2021-01-04", "2023-06-01", 225_000, (225_000 - 100_000) * 0.10 * 1.04),    # long-term, old rules
    ("2023-01-02", "2023-06-01", 100_000, 100_000 * 0.15 * 1.04),                # short-term, old rate
    ("2025-01-02", "2025-06-02", 100_000, 100_000 * 0.20 * 1.04),                # short-term, new rate
    ("2015-01-02", "2017-06-01", 500_000, 0.0),                                  # long-term was exempt then
])
def test_capital_gains_tax_by_era(cfg, buy, sell, gain, expected):
    tax, _, _ = costs.tax_for_year([{"buy_date": buy, "sell_date": sell, "gain": gain}], {}, cfg)
    assert tax == pytest.approx(expected)


def test_losses_reduce_tax_and_carry_forward(cfg):
    rows = [{"buy_date": "2025-01-02", "sell_date": "2025-06-02", "gain": -300_000},
            {"buy_date": "2023-01-02", "sell_date": "2025-06-02", "gain": 200_000}]
    tax, carry, _ = costs.tax_for_year(rows, {}, cfg)
    assert tax == 0 and carry["st"] == pytest.approx(100_000)


def test_tax_is_actually_deducted_from_paper_cash(cfg):
    cfg = replace(cfg, capital=1_000_000)
    led = Ledger(cfg)
    led.buy("X", 100, 1000.0, "2024-08-01")
    led.sell("X", None, 3000.0, "2024-12-02")                    # short-term gain of about 2 lakh
    cash = led.cash
    paid = led.settle_tax("2025-06-02")
    assert paid and paid[0]["tax"] > 35_000 and led.cash == pytest.approx(cash - paid[0]["tax"])
    assert led.settle_tax("2025-09-01") == []                    # never charged twice


# ---- 4. benchmark ---------------------------------------------------------------
def test_xirr_and_sip_on_a_known_series(cfg):
    days = pd.bdate_range("2015-01-01", "2025-01-01")
    bench = pd.Series(100 * 1.12 ** (np.arange(len(days)) / 261), index=days)
    idx = index_fund(bench, days[0], days[-1], cfg)
    assert 0.10 < idx["sip_xirr"] < 0.12 and 0.105 < idx["lump_cagr"] < 0.12   # 12% less exit tax
    assert xirr([(pd.Timestamp("2020-01-01"), -100.0), (pd.Timestamp("2021-01-01"), 110.0)]) == pytest.approx(0.10, abs=1e-3)


def test_report_has_every_number_asked_for(cfg, market):
    r = run_backtest(market, cfg, years=5, log=lambda *_: None)
    s, i = r["strategy"], r["index_fund"]
    for k in ("cagr", "max_drawdown", "worst_year", "worst_year_return", "hit_rate"):
        assert s[k] is not None
    assert i["sip_xirr"] is not None and i["lump_cagr"] is not None
    assert r["verdict"] in ("BEAT", "LOST", "MIXED") and r["caveats"]
    assert s["charges_paid"] > 0


def test_verdict_says_lost_when_the_index_wins(cfg, market):
    """Make the rules unable to buy anything: cash earns 0%, the index wins, and the report must say so."""
    r = run_backtest(market, replace(cfg, roce_min=9.9), years=5, log=lambda *_: None)
    assert r["verdict"] == "LOST" and "NOT" in r["headline"]


# ---- 5. bots ----------------------------------------------------------------------
def test_risk_limits_hold(cfg):
    led = Ledger(cfg)
    cands = [{"symbol": f"S{i}", "name": f"S{i}", "sector": "Healthcare" if i < 12 else f"Sector{i % 6}"} for i in range(40)]
    prices = {c["symbol"]: 250.0 for c in cands}
    orders, notes = risk_manager.plan_buys(led, cands, prices, cfg)
    for o in orders:
        c = next(c for c in cands if c["symbol"] == o["symbol"])
        led.buy(o["symbol"], o["qty"], prices[o["symbol"]], "2024-01-01", c["name"], c["sector"])
    eq = led.equity(prices)
    assert cfg.min_stocks <= len(led.positions) <= cfg.max_stocks
    assert max(led.qty(s) * 250.0 / eq for s in led.positions) <= cfg.max_stock_w + 1e-6
    assert max(risk_manager.sector_weights(led, prices).values()) <= cfg.max_sector_w + 1e-6
    assert led.cash >= 0


def test_no_buy_above_the_per_trade_budget(cfg):
    assert cfg.capital == 50_000 and cfg.max_trade_value == 15_000
    big = replace(cfg, capital=1_000_000)             # 10% would be Rs 1,00,000; the cap must win
    led = Ledger(big)
    cands = [{"symbol": f"S{i}", "name": "", "sector": f"Sec{i}"} for i in range(20)]
    prices = {c["symbol"]: 730.0 for c in cands}
    orders, _ = risk_manager.plan_buys(led, cands, prices, big)
    assert orders and all(o["qty"] * 730.0 * (1 + big.slippage) <= big.max_trade_value for o in orders)


def test_share_too_expensive_for_small_capital_is_skipped_with_a_reason(cfg):
    led = Ledger(cfg)
    cands = [{"symbol": "PRICEY", "name": "", "sector": "A"}, {"symbol": "OK", "name": "", "sector": "B"}]
    orders, notes = risk_manager.plan_buys(led, cands, {"PRICEY": 9000.0, "OK": 4000.0}, cfg)
    assert [o["symbol"] for o in orders] == ["OK"] and orders[0]["qty"] == 1
    assert any("PRICEY" in n and "one share costs" in n for n in notes)


def test_too_few_good_companies_means_cash_not_junk(cfg):
    led = Ledger(cfg)
    cands = [{"symbol": f"S{i}", "name": "", "sector": f"Sec{i}"} for i in range(4)]
    orders, notes = risk_manager.plan_buys(led, cands, {c["symbol"]: 100.0 for c in cands}, cfg)
    assert len(orders) == 4 and all(o["weight"] <= 1 / cfg.min_stocks + 1e-9 for o in orders)
    assert any("stays in cash" in n for n in notes)


def test_overlap_with_real_holdings_is_flagged(cfg):
    led = Ledger(cfg)
    real = {"holdings": [{"symbol": "S0", "quantity": 10, "last_price": 100.0}]}
    _, notes = risk_manager.plan_buys(led, [{"symbol": "S0", "name": "", "sector": "A"}], {"S0": 100.0}, cfg, real)
    assert any("already own" in n for n in notes)


def test_good_company_at_a_bad_price_is_rejected(cfg, market):
    A, _ = assess(market, D, cfg)
    good = next(a for a in A if a["qualifies"])
    pricey = SyntheticData(cfg, seed=7)
    s = pricey.stock(good["symbol"])
    recent = s.close.index > D - pd.Timedelta(days=60)
    s.close[recent] *= 3
    s._pe = None
    B, _ = assess(pricey, D, cfg)
    again = next(a for a in B if a["symbol"] == good["symbol"])
    assert again["analyst"]["passed"] and not again["valuation"]["ok"] and not again["qualifies"]


def test_must_pass_two_frameworks(cfg, market):
    A, _ = assess(market, D, cfg)
    assert all(a["judge"]["n_passed"] >= cfg.min_frameworks for a in A if a["qualifies"])
    assert all(a["triggers"] for a in A if a["qualifies"])


# ---- 6. paper only ------------------------------------------------------------------
def test_kite_link_can_only_read_holdings():
    assert kite_readonly.ALLOWED_TOOLS == {"login", "get_holdings"}
    for tool in ("place_order", "modify_order", "cancel_order", "place_gtt_order", "modify_gtt_order",
                 "delete_gtt_order", "get_orders", "get_positions"):
        with pytest.raises(PermissionError):
            kite_readonly.guard(tool)


def test_ledger_is_offline_bookkeeping():
    src = inspect.getsource(ledger_mod)
    assert ledger_mod.MODE == "PAPER" and stockguy.PAPER_ONLY
    for word in ("requests", "kiteconnect", "http", "socket", "mcp"):
        assert word not in src.lower().replace("paper", "")


def test_no_broker_sdk_anywhere():
    from pathlib import Path
    for f in Path(stockguy.__file__).parent.rglob("*.py"):
        text = f.read_text()
        assert "kiteconnect" not in text, f
        if f.name != "kite_readonly.py":
            assert "place_order" not in text and "kite.trade" not in text, f


# ---- 7. buy now, cash out later ------------------------------------------------------
def test_buy_then_cash_out_adds_up(cfg, market, tmp_path):
    from stockguy import app
    cfg = replace(cfg, data_dir=tmp_path)
    assert app.cmd_cashout(market, cfg, log=lambda *_: None) is None          # nothing bought yet
    A = app.cmd_scout(market, cfg, live_events=False, log=lambda *_: None)
    app.cmd_review(market, cfg, A=A, force=True, log=lambda *_: None)         # no backtest on file: needs force
    led = Ledger(cfg, tmp_path / "ledger.json")
    assert led.positions and all(t["value"] <= cfg.max_trade_value for t in led.trades)
    preview = app.cmd_cashout(market, cfg, log=lambda *_: None)
    assert (tmp_path / "ledger.json").exists() and not preview["confirmed"]   # a preview sells nothing
    final = app.cmd_cashout(market, cfg, confirm=True, log=lambda *_: None)
    assert final["cash_out"] == pytest.approx(preview["cash_out"], abs=1.0)
    assert final["cash_out"] < cfg.capital                                    # same-day round trip only loses charges
    assert not (tmp_path / "ledger.json").exists() and list(tmp_path.glob("ledger_closed_*.json"))
