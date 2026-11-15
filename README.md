# Stock Guy

Six bots that look for Indian companies worth holding for 3-5 years.
**Paper mode only.** It keeps a pretend portfolio of Rs 50,000 in a file on your computer.
It cannot place a real order. There is no code in here that can.

## One command

```bash
pip install -r requirements.txt
python run.py
```

That does everything that is due, in this order, and then opens nothing, sends nothing, buys nothing real:

1. **Backtest first.** Tests the rules on the past and compares them with a Nifty 50 index fund.
2. **Scout.** Refreshes triggers and the scored watchlist.
3. **Quarterly review**, only if a quarter has passed. Updates the paper portfolio and the journal.
4. Builds **`dashboard/index.html`**. Double-click it.

The first run downloads data for about 500-900 companies and can take 30-60 minutes. Later runs use the cache.

### If you forget everything else

| When | Do this | Takes |
|---|---|---|
| Once a week (or whenever you remember) | `python run.py` then open `dashboard/index.html` | 5 min |
| Once a quarter (Feb, May, Aug, Nov) | the same command; it knows a review is due | 15 min reading the journal |
| Never | check it daily | |

Missing a week does no harm. Triggers stay on the watchlist for about six months.

### Other commands

```bash
python run.py doctor      # which free data sources are reachable from this computer
python run.py test        # the safety tests (no internet needed)
python run.py backtest    # only the backtest      (--years 10)
python run.py scout       # only the weekly scout
python run.py review      # only the quarterly review   (--force to run it early)
python run.py holdings    # read your Zerodha holdings, read-only
python run.py --demo      # try everything on made-up companies, no internet
```

## Read this first: what has and has not been tested

**The real 10-year backtest has not been run yet.** The machine this was built on had no access to
Yahoo Finance or NSE. Run `python run.py` on your own computer to get the real verdict. Until then,
nobody knows whether these rules beat the index fund. Do not assume they do.

What *has* been verified, with 26 automated tests on a made-up market (`python run.py test`):

| Test you asked for | How it is enforced | Checked by |
|---|---|---|
| No look-ahead | Bots only see a `StockView` cut off at the decision date. Annual results count 90 days after year end, quarterly 60 days. Trades happen at the next day's close. | Scrambling all future data leaves past decisions byte-identical. A deliberately planted leak makes the tests fail. |
| No survivorship bias | Universe on each date = the Nifty 500 list as it was then (Wayback Machine copies of NSE's file). A holding that stops trading is written off at a 50% haircut. | Failed companies are pickable while alive; a dead holding is written off. |
| Brokerage, STT, tax | Zerodha delivery: Rs 0 brokerage, 0.1% STT each way, exchange + SEBI charges, 18% GST, 0.015% stamp duty, Rs 15.34 DP charge, 0.1% slippage. Capital gains tax by the rules in force on the sale date (exempt before Apr 2018; 10% / 15%; then 12.5% / 20% from 23 Jul 2024), Rs 1L / 1.25L exemption, loss set-off, 4% cess. | Charges and each tax era checked to the rupee. |
| Versus Nifty 50 SIP | NIFTYBEES (a real index ETF, costs already inside its price). Monthly SIP (XIRR) and lump sum (CAGR), both after exit tax, same dates. | Known-answer test. |
| CAGR, max drawdown, worst year, hit rate | All in the report and on the dashboard. | Report completeness test. |
| Honest verdict | Prints "did NOT beat the index fund" when it does not. "Mixed" is not called a win. | A test forces a loss and checks the wording. |

### Limits you should know about (these are real, not fine print)

1. **Free data does not go back 10 years for financials.** Yahoo gives roughly 4 years of annual
   statements per company. The rules need at least 3 years before they will judge a company, so with
   Yahoo alone the backtest can only cover roughly the last 1-2 years. The report states the period it
   really tested. To get a true 10-year test you need longer history: see `data/fundamentals_csv/README.md`.
2. **Delisted companies mostly have no data on Yahoo.** They are counted and reported, but the rules
   could never have picked them, so some survivorship bias remains. The report tells you how many.
3. **Promoter holding and pledging** have no free history. Live runs show Yahoo's insider-holding figure
   as a rough stand-in and mark pledging "unknown". Check pledging yourself before trusting a pick.
4. **The Scout's news triggers** (new CEO, M&A, orders, capacity, promoter buying) work live from NSE
   filings and RSS, by keyword. They cannot be backtested, and a keyword cannot tell whether a new CEO
   is "experienced". Only results and debt-reduction triggers are backtested.
5. **The four frameworks are number-based stand-ins.** Agrawal, Kedia, Jhunjhunwala and Damani use
   judgement a program does not have. A "pass" means "worth your reading time".
6. The code that talks to Yahoo, NSE, the Wayback Machine and Kite was written without being able to
   reach them. Run `python run.py doctor` first. Expect to fix a rough edge or two on the first real run.

## Small capital: what Rs 50,000 changes

- With 15-20 stocks and the 10% rule, each position is about Rs 2,500-5,000. The Rs 15,000 per-trade
  cap is a ceiling; the 10% rule is tighter and applies first.
- Only whole shares can be bought. A company whose single share costs more than Rs 5,000 (10% of the
  portfolio) is skipped, and the journal says so.
- Fixed charges bite harder: the Rs 15.34 DP charge on a Rs 3,000 sale is about 0.5%.

## The six bots (one file each, in `stockguy/bots/`)

| Bot | File | When | What it does |
|---|---|---|---|
| 1. Scout | `scout.py` | weekly | Finds triggers: strong quarterly results, debt reduction, new CEO/MD, M&A, large orders, new capacity, promoter buying. |
| 2. Business Analyst | `business_analyst.py` | on trigger | Up to 10 years: sales and profit growth, ROCE above 15% (ROE for lenders), debt under 0.5x equity, cash flow backing profit, no loss years, promoter holding/pledge when known. Unknown = not passed. |
| 3. Framework Judge | `framework_judge.py` | on trigger | Scores QGLP, SMILE, GARP and Damani-style value out of 100 each. Must pass at least two. |
| 4. Valuation Analyst | `valuation_analyst.py` | on trigger | PE against its own 5-year median and against sector peers, plus PEG. Rejects good companies at bad prices. |
| 5. Risk Manager | `risk_manager.py` | quarterly | Max Rs 15,000 a buy, 10% a stock, 25% a sector, 15-20 stocks. If fewer than 15 pass, the rest stays in cash. Flags overlap with your real Zerodha holdings. |
| 6. Reviewer | `reviewer.py` | quarterly | Re-checks every holding against the reason it was bought. Sells on a broken business, not on a falling price. Writes `data/journal.md` in plain English. |

Every threshold is in `stockguy/config.py`, with a comment. Change them there.
If you change rules after seeing a backtest and re-run until it looks good, the result stops meaning anything.

## Zerodha (read-only)

Your real holdings are only *read*, to check overlap. Three locks keep it that way:

- `stockguy/kite_readonly.py` can call exactly two tools on the official Kite MCP server
  (`https://mcp.kite.trade/mcp`): `login` and `get_holdings`. Anything else raises an error.
- `.claude/settings.json` denies `place_order`, `modify_order`, `cancel_order` and all three GTT order tools for Claude.
- `CLAUDE.md` tells any AI assistant the same.

Two ways to sync:

```bash
python run.py holdings                      # opens a Zerodha login link; you sign in in your browser
python run.py holdings --csv holdings.csv   # or: Zerodha Console > Holdings > Download, no login from here
```

Or, in Claude Code with this repo open, say "sync my Zerodha holdings".
The copy is saved to `data/holdings.json`, which git ignores.

## Where things are

```
run.py                     the one command
stockguy/config.py         every rule and threshold
stockguy/bots/             the six bots
stockguy/data.py           data, and the cut-off-at-a-date view that prevents look-ahead
stockguy/costs.py          charges and capital gains tax
stockguy/ledger.py         the paper ledger (a JSON file; no broker code)
stockguy/backtest.py       the backtest and the index fund comparison
stockguy/dashboard.py      builds dashboard/index.html
stockguy/synthetic.py      made-up market for tests and --demo
tests/                     26 safety tests
data/                      ledger.json, journal.md, watchlist.json, backtest_report.json, cache/
```

## Not included, on purpose

No paid API. No real-order feature. Either one needs your explicit yes first.

This is a screening and record-keeping tool, not investment advice.
