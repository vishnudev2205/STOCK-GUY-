# Optional: longer financial history

Yahoo's free data has only about 4 years of annual results per company. To let the
Business Analyst and the backtest see 5-10 years, drop one CSV per company here,
named `<NSE SYMBOL>.csv` (for example `TITAN.csv`), one row per financial year:

```
period_end,revenue,net_income,ebit,total_assets,current_liabilities,total_debt,equity,cfo,capex,shares
2016-03-31,112760000000,6740000000,9330000000,...
```

- Amounts in rupees (not crores). `capex` is negative. `shares` is the number of shares at year end.
- Optional column `available`: the date the result was actually announced. If missing, the
  system assumes year end + 90 days.
- Rows here override Yahoo for the same year.
