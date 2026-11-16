"""Builds dashboard/index.html: one self-contained file, no server, opens in any browser."""
from __future__ import annotations

import json
from pathlib import Path

from . import app
from .config import Config, ROOT


def build(provider, cfg: Config) -> Path:
    rep = app._load(cfg.data_dir / "backtest_report.json", None)
    if rep:
        rep = {k: v for k, v in rep.items() if k not in ("journal", "closed", "coverage")}
    data = {
        "backtest": rep,
        "watchlist": app._load(cfg.data_dir / "watchlist.json", None),
        "portfolio": app.portfolio_snapshot(provider, cfg),
        "journal": app._load(cfg.data_dir / "journal.json", []),
        "trade_log": _trade_log(provider, cfg),
        "source": provider.label, "synthetic": "SYNTHETIC" in provider.label,
    }
    out = ROOT / "dashboard" / ("demo.html" if data["synthetic"] else "index.html")
    out.parent.mkdir(exist_ok=True)
    blob = json.dumps(data, default=app._json).replace("</", "<\\/")
    out.write_text(TEMPLATE.replace("__DATA__", blob))
    return out


def _trade_log(provider, cfg: Config) -> list[dict]:
    from . import trade_log
    from .ledger import Ledger
    ledger = Ledger(cfg, cfg.data_dir / "ledger.json")
    today = provider.calendar()[-1]
    prices = {}
    for s in ledger.positions:
        v = provider.view(s, today)
        if v is not None and v.has_price:
            prices[s] = v.trade_price
    return trade_log.rows(ledger, prices)


TEMPLATE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Stock Guy</title>
<style>
:root{--bg:#f6f5f2;--card:#fcfcfb;--ink:#0b0b0b;--ink2:#52514e;--mut:#7a7973;--line:#e3e1da;--s1:#2a78d6;--s2:#eb6834;
--good:#0b7a3b;--bad:#b3261e;--warn:#8a5a00;--chip:#eceae4}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){--bg:#121211;--card:#1a1a19;--ink:#f0efec;--ink2:#c3c2b7;--mut:#8f8e86;
--line:#30302d;--s1:#3987e5;--s2:#d95926;--good:#4cc27d;--bad:#f2867f;--warn:#e0b050;--chip:#2a2a27}}
:root[data-theme=dark]{--bg:#121211;--card:#1a1a19;--ink:#f0efec;--ink2:#c3c2b7;--mut:#8f8e86;--line:#30302d;--s1:#3987e5;--s2:#d95926;
--good:#4cc27d;--bad:#f2867f;--warn:#e0b050;--chip:#2a2a27}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
.wrap{max-width:1180px;margin:0 auto;padding:16px}
header{display:flex;flex-wrap:wrap;gap:10px;align-items:center;margin-bottom:12px}
h1{font-size:22px;margin:0}h2{font-size:17px;margin:0 0 10px}h3{font-size:14px;margin:14px 0 6px;color:var(--ink2)}
.badge{font-size:12px;font-weight:600;padding:3px 9px;border-radius:99px;background:var(--chip);color:var(--ink2);white-space:nowrap}
.muted{color:var(--mut);font-size:13px}
.banner{border:2px solid var(--bad);color:var(--bad);border-radius:10px;padding:10px 14px;font-weight:600;margin-bottom:12px;background:var(--card)}
nav{display:flex;gap:4px;border-bottom:1px solid var(--line);margin-bottom:14px;overflow-x:auto}
nav button{background:none;border:0;border-bottom:3px solid transparent;padding:9px 14px;font:inherit;color:var(--ink2);cursor:pointer;white-space:nowrap}
nav button[aria-selected=true]{color:var(--ink);border-color:var(--s1);font-weight:600}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px;margin-bottom:14px}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:10px;margin-bottom:14px}
.tile{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px 14px}
.tile .k{font-size:12.5px;color:var(--ink2)}.tile .v{font-size:24px;font-weight:650;font-variant-numeric:tabular-nums}.tile .s{font-size:12.5px;color:var(--mut)}
.verdict{font-size:18px;font-weight:650;display:flex;gap:10px;align-items:flex-start}
.verdict .ic{flex:none;width:28px;height:28px;border-radius:50%;display:grid;place-items:center;color:#fff;font-weight:700}
.tw{overflow-x:auto}table{border-collapse:collapse;width:100%;font-size:13.5px}
th,td{text-align:left;padding:7px 9px;border-bottom:1px solid var(--line);vertical-align:top}
th{font-size:12px;color:var(--ink2);font-weight:600;white-space:nowrap}td.n,th.n{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
tr.row{cursor:pointer}tr.row:hover{background:var(--chip)}
.chip{display:inline-block;font-size:11.5px;padding:1px 7px;border-radius:99px;background:var(--chip);color:var(--ink2);margin:1px 3px 1px 0;white-space:nowrap}
.pass{color:var(--good);font-weight:600}.fail{color:var(--mut)}.neg{color:var(--bad)}.pos{color:var(--good)}
.controls{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:10px;align-items:center}
select,input[type=search]{font:inherit;padding:6px 9px;border:1px solid var(--line);border-radius:8px;background:var(--card);color:var(--ink)}
.bar{height:8px;background:var(--chip);border-radius:4px;position:relative;min-width:90px}.bar i{position:absolute;left:0;top:0;bottom:0;border-radius:4px;background:var(--s1)}
.bar b{position:absolute;top:-3px;bottom:-3px;width:2px;background:var(--ink2)}
.detail td{background:var(--bg);font-size:13px}.detail ul{margin:4px 0 8px 18px;padding:0}
.j{padding:8px 0;border-bottom:1px solid var(--line)}.j:last-child{border:0}
.act{font-size:11.5px;font-weight:700;padding:1px 7px;border-radius:5px;margin-right:6px;border:1px solid currentColor}
.legend{display:flex;gap:16px;font-size:13px;color:var(--ink2);margin-bottom:4px}.legend i{display:inline-block;width:14px;height:3px;border-radius:2px;vertical-align:middle;margin-right:6px}
#tip{position:absolute;pointer-events:none;background:var(--card);border:1px solid var(--line);border-radius:8px;padding:6px 9px;font-size:12.5px;box-shadow:0 2px 10px #0003;display:none;white-space:nowrap}
.chart{position:relative}svg text{fill:var(--mut);font-size:11.5px}
ul.cav{margin:0;padding-left:18px}ul.cav li{margin-bottom:5px}
</style></head><body><div class="wrap">
<header><h1>Stock Guy</h1><span class="badge">PAPER MODE &middot; no real orders</span><span class="muted" id="src"></span></header>
<div id="banner"></div>
<nav role="tablist"></nav><main id="main"></main>
<p class="muted">Not investment advice. A screening tool with a simulated ledger. Scores are number-based stand-ins for how these investors think, not their opinions.</p>
</div><div id="tip"></div>
<script id="d" type="application/json">__DATA__</script>
<script>
const D=JSON.parse(document.getElementById('d').textContent),$=s=>document.querySelector(s);
const pct=(v,d=1)=>v==null?'n/a':(v*100).toFixed(d)+'%',sp=(v,d=1)=>v==null?'n/a':(v>0?'+':'')+(v*100).toFixed(d)+'%';
const rs=v=>v==null?'n/a':'₹'+Math.round(v).toLocaleString('en-IN'),num=(v,d=1)=>v==null?'n/a':(+v).toFixed(d);
const esc=s=>String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const cls=v=>v==null?'':v<0?'neg':'pos';
$('#src').textContent='Data: '+D.source+(D.watchlist?' · as of '+D.watchlist.asof:'');
if(D.synthetic)$('#banner').innerHTML='<div class="banner">MADE-UP TEST DATA. These are not real companies and these are not real results. This page only shows that the software works.</div>';
const TABS={'Trade log':tradelog,Verdict:verdict,Watchlist:watchlist,'Paper portfolio':portfolio,Journal:journal};
const nav=$('nav');Object.keys(TABS).forEach((t,i)=>{const b=document.createElement('button');b.textContent=t;b.setAttribute('role','tab');
b.onclick=()=>{[...nav.children].forEach(x=>x.setAttribute('aria-selected',x===b));$('#main').innerHTML='';TABS[t]($('#main'));try{localStorage.setItem('sg-tab',t)}catch(e){}};nav.appendChild(b)});
let start='Verdict';try{start=localStorage.getItem('sg-tab')||start}catch(e){}
([...nav.children].find(b=>b.textContent===start)||nav.children[0]).click();

function verdict(el){const B=D.backtest;
 if(!B){el.innerHTML='<div class="card"><h2>The backtest has not run yet</h2><p>Run <code>python run.py</code>. Tests come first: the paper portfolio stays empty until the 10-year test has run.</p></div>';return}
 if(!B.ok){el.innerHTML='<div class="card"><h2>The backtest could not run</h2><p>'+esc(B.problem)+'</p></div>';return}
 const s=B.strategy,x=B.index_fund,col={BEAT:'var(--good)',LOST:'var(--bad)',MIXED:'var(--warn)'}[B.verdict],ic={BEAT:'✓',LOST:'✗',MIXED:'!'}[B.verdict];
 el.innerHTML=`<div class="card"><div class="verdict"><span class="ic" style="background:${col}">${ic}</span><span>${esc(B.headline)}</span></div>
 <p class="muted" style="margin:8px 0 0">Tested ${B.start} to ${B.end}: ${B.tested_years} years (you asked for ${B.requested_years}). All figures after charges and tax.</p></div>
 <div class="tiles">
 <div class="tile"><div class="k">Return per year, these rules</div><div class="v">${sp(s.cagr)}</div><div class="s">Index fund lump sum ${sp(x.lump_cagr)} &middot; SIP ${sp(x.sip_xirr)}</div></div>
 <div class="tile"><div class="k">Biggest fall from a peak</div><div class="v">${pct(s.max_drawdown)}</div><div class="s">${s.drawdown_peak} to ${s.drawdown_trough} &middot; index ${pct(x.lump_max_drawdown)}</div></div>
 <div class="tile"><div class="k">Worst calendar year</div><div class="v">${sp(s.worst_year_return)}</div><div class="s">in ${s.worst_year} &middot; index ${sp(x.lump_worst_year_return)} in ${x.lump_worst_year}</div></div>
 <div class="tile"><div class="k">Hit rate</div><div class="v">${pct(s.hit_rate,0)}</div><div class="s">of ${s.purchases} purchases made money &middot; ${pct(s.beat_index_rate,0)} beat the index</div></div></div>
 <div class="card"><h2>${rs(B.capital)} invested at the start</h2><div class="legend"><span><i style="background:var(--s1)"></i>These rules</span><span><i style="background:var(--s2)"></i>Nifty 50 index fund (lump sum)</span></div>
 <div class="chart" id="ch"></div><p class="muted">Before exit tax. The SIP is not drawn because its money goes in gradually; compare it using the return-per-year figure.</p></div>
 <div class="card"><h2>Side by side</h2><div class="tw"><table><tr><th></th><th class="n">These rules</th><th class="n">Index fund, lump sum</th><th class="n">Index fund, monthly SIP</th></tr>
 <tr><td>Return per year</td><td class="n">${sp(s.cagr)}</td><td class="n">${sp(x.lump_cagr)}</td><td class="n">${sp(x.sip_xirr)}</td></tr>
 <tr><td>${rs(B.capital)} ends as (after all tax)</td><td class="n">${rs(s.final_after_exit_tax)}</td><td class="n">${rs(x.lump_final)}</td><td class="n">${rs(x.sip_final)}</td></tr>
 <tr><td>Biggest fall</td><td class="n">${pct(s.max_drawdown)}</td><td class="n">${pct(x.lump_max_drawdown)}</td><td class="n">n/a</td></tr>
 <tr><td>Worst year</td><td class="n">${s.worst_year}: ${sp(s.worst_year_return)}</td><td class="n">${x.lump_worst_year}: ${sp(x.lump_worst_year_return)}</td><td class="n">n/a</td></tr>
 <tr><td>Charges paid</td><td class="n">${rs(s.charges_paid)}</td><td class="n" colspan="2">inside the fund's price</td></tr>
 <tr><td>Tax paid along the way / due on exit</td><td class="n">${rs(s.tax_paid)} / ${rs(s.exit_tax_and_charges)}</td><td class="n" colspan="2">only on exit (already deducted above)</td></tr>
 <tr><td>Average stocks held / cash idle</td><td class="n">${num(s.avg_holdings,0)} / ${pct(s.avg_cash_share,0)}</td><td class="n" colspan="2"></td></tr></table></div>
 <h3>Year by year</h3><div class="tw"><table><tr><th>Year</th>${Object.keys(s.yearly).map(y=>`<th class="n">${y}</th>`).join('')}</tr>
 <tr><td>These rules</td>${Object.values(s.yearly).map(v=>`<td class="n ${cls(v)}">${sp(v,0)}</td>`).join('')}</tr>
 <tr><td>Index fund</td>${Object.keys(s.yearly).map(y=>{const v=x.lump_yearly[y];return `<td class="n ${cls(v)}">${sp(v,0)}</td>`}).join('')}</tr></table></div></div>
 <div class="card"><h2>Read this before trusting the numbers</h2><ul class="cav">${B.caveats.map(c=>'<li>'+esc(c)+'</li>').join('')}</ul></div>`;
 chart($('#ch'),B.curve)}

function chart(el,c){const W=1100,H=340,L=56,R=16,T=12,Bm=28,n=c.dates.length;if(n<2)return;
 const max=Math.max(...c.strategy,...c.index)*1.05,min=Math.min(0,...c.strategy,...c.index);
 const X=i=>L+(W-L-R)*i/(n-1),Y=v=>T+(H-T-Bm)*(1-(v-min)/(max-min));
 const lakh=v=>v>=1e7?(v/1e7).toFixed(v>=1e8?0:1)+' Cr':v>=1e5?(v/1e5).toFixed(v>=1e6?0:1)+' L':Math.round(v).toLocaleString('en-IN');
 let g='';const step=Math.pow(10,Math.floor(Math.log10(max)))/2,st=max/step>8?step*2:step;
 for(let v=0;v<=max;v+=st)g+=`<line x1="${L}" x2="${W-R}" y1="${Y(v)}" y2="${Y(v)}" stroke="var(--line)"/><text x="${L-8}" y="${Y(v)+4}" text-anchor="end">${v?"₹"+lakh(v):"0"}</text>`;
 let last='';c.dates.forEach((d,i)=>{const y=d.slice(0,4);if(y!==last&&d.slice(5,7)==='01'){g+=`<text x="${X(i)}" y="${H-8}" text-anchor="middle">${y}</text>`}last=y});
 const path=a=>a.map((v,i)=>(i?'L':'M')+X(i).toFixed(1)+' '+Y(v).toFixed(1)).join('');
 el.innerHTML=`<svg viewBox="0 0 ${W} ${H}" width="100%" role="img" aria-label="Growth of the starting capital: these rules versus the index fund">${g}
 <path d="${path(c.index)}" fill="none" stroke="var(--s2)" stroke-width="2" stroke-linejoin="round"/>
 <path d="${path(c.strategy)}" fill="none" stroke="var(--s1)" stroke-width="2" stroke-linejoin="round"/>
 <line id="cx" y1="${T}" y2="${H-Bm}" stroke="var(--mut)" stroke-dasharray="3 3" visibility="hidden"/>
 <circle id="c1" r="4.5" fill="var(--s1)" stroke="var(--card)" stroke-width="2" visibility="hidden"/><circle id="c2" r="4.5" fill="var(--s2)" stroke="var(--card)" stroke-width="2" visibility="hidden"/>
 <rect x="${L}" y="${T}" width="${W-L-R}" height="${H-T-Bm}" fill="transparent" id="hit"/></svg>`;
 const svg=el.querySelector('svg'),tip=$('#tip'),q=s=>svg.querySelector(s);
 q('#hit').addEventListener('pointermove',e=>{const r=svg.getBoundingClientRect(),px=(e.clientX-r.left)/r.width*W;
  const i=Math.max(0,Math.min(n-1,Math.round((px-L)/(W-L-R)*(n-1))));
  q('#cx').setAttribute('x1',X(i));q('#cx').setAttribute('x2',X(i));q('#c1').setAttribute('cx',X(i));q('#c1').setAttribute('cy',Y(c.strategy[i]));
  q('#c2').setAttribute('cx',X(i));q('#c2').setAttribute('cy',Y(c.index[i]));['#cx','#c1','#c2'].forEach(s=>q(s).setAttribute('visibility','visible'));
  tip.innerHTML=`<b>${c.dates[i].slice(0,7)}</b><br>These rules ${rs(c.strategy[i])}<br>Index fund ${rs(c.index[i])}`;tip.style.display='block';
  const tx=e.pageX+14+tip.offsetWidth>document.documentElement.clientWidth?e.pageX-tip.offsetWidth-14:e.pageX+14;tip.style.left=tx+'px';tip.style.top=(e.pageY-10)+'px'});
 q('#hit').addEventListener('pointerleave',()=>{tip.style.display='none';['#cx','#c1','#c2'].forEach(s=>q(s).setAttribute('visibility','hidden'))})}

function watchlist(el){const Wl=D.watchlist;
 if(!Wl){el.innerHTML='<div class="card"><h2>No watchlist yet</h2><p>Run <code>python run.py scout</code>.</p></div>';return}
 const rows=Wl.rows,fw=rows.find(r=>r.frameworks&&Object.keys(r.frameworks).length),names=fw?Object.keys(fw.frameworks):[];
 const short=n=>n.split(' (')[0];
 el.innerHTML=`${(Wl.problems||[]).map(p=>'<div class="card" style="border-color:var(--warn)">'+esc(p)+'</div>').join('')}
 <div class="card"><h2>Watchlist with scores</h2><p class="muted">${Wl.stats.members} index members &middot; ${Wl.stats.analysable} with enough history &middot; ${Wl.stats.candidates} buy candidates. Click a row for the full working.</p>
 <div class="controls"><select id="f"><option value="cand">Buy candidates</option><option value="near">Passed business + frameworks</option><option value="held">In paper portfolio</option><option value="trig">Has a trigger</option><option value="all">Everything</option></select>
 <input type="search" id="q" placeholder="Search company"><span class="muted" id="cnt"></span></div>
 <div class="tw"><table><thead><tr><th>Company</th><th>Status</th><th class="n">Score</th><th class="n">Business</th>${names.map(n=>`<th class="n" title="${esc(n)}">${short(n)}</th>`).join('')}<th class="n">PE</th><th class="n">PEG</th><th>Triggers</th></tr></thead><tbody id="tb"></tbody></table></div></div>`;
 const tb=$('#tb');
 const draw=()=>{const f=$('#f').value,q=$('#q').value.toLowerCase();
  const sel=rows.filter(r=>(f==='all'||f==='cand'&&r.qualifies||f==='held'&&r.held||f==='trig'&&r.triggers&&r.triggers.length||f==='near'&&r.n_frameworks>=2&&r.business===100)&&(!q||(r.name+r.symbol).toLowerCase().includes(q)));
  $('#cnt').textContent=sel.length+' shown';
  tb.innerHTML=sel.length?sel.map((r,i)=>`<tr class="row" data-i="${rows.indexOf(r)}"><td><b>${esc(r.symbol)}</b>${r.held?' <span class="chip">held</span>':''}<br><span class="muted">${esc(r.name)} &middot; ${esc(r.sector)}</span></td>
  <td class="${r.qualifies?'pass':''}">${esc(r.stage)}</td><td class="n">${num(r.score,0)}</td><td class="n">${r.business??''}</td>
  ${names.map(n=>{const x=r.frameworks&&r.frameworks[n];return `<td class="n ${x&&x.passed?'pass':'fail'}">${x?x.score+(x.passed?' ✓':''):''}</td>`}).join('')}
  <td class="n">${r.pe?num(r.pe,0):''}${r.pe_median?'<br><span class="muted">usual '+num(r.pe_median,0)+'</span>':''}</td><td class="n">${r.peg?num(r.peg):''}</td>
  <td>${(r.triggers||[]).map(t=>'<span class="chip">'+esc(t.type)+'</span>').join('')}</td></tr>`).join(''):'<tr><td colspan="12" class="muted">Nothing matches. That is normal: the bar is high on purpose.</td></tr>'};
 tb.onclick=e=>{const tr=e.target.closest('tr.row');if(!tr)return;if(tr.nextSibling&&tr.nextSibling.className==='detail'){tr.nextSibling.remove();return}
  const r=rows[+tr.dataset.i],d=document.createElement('tr');d.className='detail';
  d.innerHTML=`<td colspan="12"><b>Business Analyst</b> (${r.years??0} years of results)<ul>${(r.checks||[]).map(c=>`<li>${c.ok===true?'✓':c.ok===false?'✗':'?'} ${esc(c.name)}: ${esc(c.value)}</li>`).join('')}${(r.notes||[]).map(n=>'<li>'+esc(n)+'</li>').join('')}</ul>
  <b>Framework Judge</b><ul>${Object.entries(r.frameworks||{}).map(([k,f])=>`<li>${f.passed?'✓':'✗'} ${esc(k)}: ${f.score}/100 (${Object.entries(f.parts).map(([a,b])=>esc(a)+' '+b).join(', ')})</li>`).join('')}</ul>
  <b>Valuation Analyst</b><ul>${(r.price_reasons||[]).map(x=>'<li>'+esc(x)+'</li>').join('')}<li>Sector peers PE: ${r.peer_pe?num(r.peer_pe,0):'n/a'}</li></ul>
  <b>Scout</b><ul>${(r.triggers||[]).map(t=>'<li>'+esc(t.type)+': '+esc(t.detail)+' ('+esc(t.date)+')</li>').join('')||'<li>No fresh trigger.</li>'}</ul></td>`;tr.after(d)};
 $('#f').onchange=draw;$('#q').oninput=draw;if(!rows.some(r=>r.qualifies))$('#f').value='near';draw()}

function portfolio(el){const P=D.portfolio,gain=P.equity/P.start-1,O=P.overlap;
 el.innerHTML=`<div class="tiles"><div class="tile"><div class="k">Paper portfolio value</div><div class="v">${rs(P.equity)}</div><div class="s">started with ${rs(P.start)} &middot; ${sp(gain)}</div></div>
 <div class="tile"><div class="k">Cash</div><div class="v">${rs(P.cash)}</div><div class="s">${pct(P.equity?P.cash/P.equity:1,0)} of the portfolio</div></div>
 <div class="tile"><div class="k">Stocks held</div><div class="v">${P.rows.length}</div><div class="s">target ${P.limits.min}-${P.limits.max}</div></div>
 <div class="tile"><div class="k">Charges and tax so far</div><div class="v">${rs(P.charges+P.tax_paid.reduce((a,t)=>a+t.tax,0))}</div><div class="s">simulated, Zerodha delivery rates</div></div></div>
 <div class="card"><h2>Holdings (simulated)</h2>${P.rows.length?`<div class="tw"><table><tr><th>Company</th><th class="n">Qty</th><th class="n">Bought at</th><th class="n">Now</th><th class="n">Gain</th><th>Share of portfolio (limit ${pct(P.limits.stock,0)})</th><th>Why it was bought</th></tr>
 ${P.rows.map(r=>`<tr><td><b>${esc(r.symbol)}</b>${r.strikes?' <span class="chip">on watch</span>':''}<br><span class="muted">${esc(r.name)} &middot; ${esc(r.sector)} &middot; since ${r.bought}</span></td><td class="n">${r.qty}</td><td class="n">${num(r.avg,0)}</td><td class="n">${r.price?num(r.price,0):'n/a'}</td>
 <td class="n ${cls(r.pnl)}">${sp(r.pnl,0)}</td><td><div class="bar"><i style="width:${Math.min(100,r.weight/0.15*100)}%"></i><b style="left:${P.limits.stock/0.15*100}%"></b></div><span class="muted">${pct(r.weight)}</span></td><td class="muted">${esc(r.why)}</td></tr>`).join('')}</table></div>`
 :'<p>No paper holdings yet. They are added at the quarterly review, and only for companies that pass every bot.</p>'}</div>
 <div class="card"><h2>Sector spread (limit ${pct(P.limits.sector,0)})</h2>${Object.keys(P.sectors).length?`<table>${Object.entries(P.sectors).sort((a,b)=>b[1]-a[1]).map(([k,v])=>`<tr><td style="width:40%">${esc(k)}</td><td><div class="bar"><i style="width:${Math.min(100,v/0.35*100)}%"></i><b style="left:${P.limits.sector/0.35*100}%"></b></div></td><td class="n">${pct(v)}</td></tr>`).join('')}</table>`:'<p class="muted">Nothing held.</p>'}</div>
 <div class="card"><h2>Overlap with your real Zerodha holdings</h2>${O.synced_at?`<p class="muted">Read-only copy from ${esc(O.synced_at)} &middot; ${O.n_real} real holdings.</p>${O.overlap.length?'<ul>'+O.overlap.map(o=>`<li><b>${esc(o.symbol)}</b> is also ${pct(o.real_weight)} of your real portfolio.</li>`).join('')+'</ul>':'<p>No overlap: nothing in the paper portfolio is in your real account.</p>'}`
 :'<p>Not connected yet. Run <code>python run.py holdings</code> (read-only), or ask Claude to "sync my Zerodha holdings".</p>'}</div>
 <div class="card"><h2>Recent paper trades</h2>${P.trades.length?`<div class="tw"><table><tr><th>When (IST)</th><th>Side</th><th>Stock</th><th class="n">Qty</th><th class="n">Price</th><th class="n">Charges</th><th>Reason</th></tr>
 ${P.trades.slice().reverse().map(t=>`<tr><td>${t.time||t.date}</td><td>${t.side}</td><td>${esc(t.symbol)}</td><td class="n">${t.qty}</td><td class="n">${num(t.price,2)}</td><td class="n">${num(t.charges,0)}</td><td class="muted">${esc(t.reason||'')}</td></tr>`).join('')}</table></div>`:'<p class="muted">None yet.</p>'}</div>`}

function tradelog(el){const T=D.trade_log||[];
 const w=t=>{if(!t)return'n/a';const d=new Date(String(t).replace(' ','T'));return isNaN(d)?esc(t):d.toLocaleDateString('en-IN',{weekday:'short',day:'numeric',month:'short',year:'numeric'})+(String(t).length>10?', '+String(t).slice(11,16)+' IST':'')};
 if(!T.length){el.innerHTML='<div class="card"><h2>Trade log</h2><p>No paper trades yet. Run <code>python run.py buy</code>.</p></div>';return}
 el.innerHTML='<div class="card"><h2>Trade log</h2><p class="muted">Every paper buy and sell: when, at what price, and why. Newest first.</p>'+T.map(r=>`<div class="j">
 <div><span class="act" style="color:var(--good)">BUY</span><b>${r.qty} × ${esc(r.symbol)}</b> <span class="muted">${esc(r.name)}</span></div>
 <div>${w(r.buy_time)} at <b>${'₹'+num(r.buy_price,2)}</b></div><div class="muted">Why: ${esc(r.buy_reason||'no reason recorded')}</div>
 ${r.open?`<div style="margin-top:4px"><span class="act" style="color:var(--ink2)">HOLD</span>Still held${r.now_price?` · now ₹${num(r.now_price,2)} <span class="${cls(r.ret)}">${sp(r.ret)}</span>`:''}</div>`
 :`<div style="margin-top:4px"><span class="act" style="color:var(--bad)">SELL</span>${w(r.sell_time)} at <b>₹${num(r.sell_price,2)}</b> · <span class="${cls(r.pnl)}">${r.pnl>=0?'+':''}${rs(r.pnl)} (${sp(r.ret)})</span></div><div class="muted">Why: ${esc(r.sell_reason||'no reason recorded')}</div>`}</div>`).join('')+'</div>'}

function journal(el){const J=D.journal;
 if(!J.length){el.innerHTML='<div class="card"><h2>Journal</h2><p>Empty. The Reviewer writes here once a quarter.</p></div>';return}
 const col={BUY:'var(--good)',SELL:'var(--bad)',WATCH:'var(--warn)',TRIM:'var(--warn)',TAX:'var(--ink2)',NOTE:'var(--ink2)',HOLD:'var(--ink2)'};
 el.innerHTML='<div class="card"><h2>Journal</h2><div class="controls"><label><input type="checkbox" id="hh"> Hide unchanged holdings</label></div><div id="jl"></div></div>';
 const draw=()=>{const hide=$('#hh').checked,days=[...new Set(J.map(e=>e.date))].sort().reverse();
  $('#jl').innerHTML=days.map(d=>{const es=J.filter(e=>e.date===d&&!(hide&&e.action==='HOLD'));return es.length?`<h3>${d}</h3>`+es.map(e=>`<div class="j"><span class="act" style="color:${col[e.action]||'var(--ink2)'}">${e.action}</span>${esc(e.text.replace(/^(BUY|SELL|HOLD|WATCH|TRIM) /,''))}</div>`).join(''):''}).join('')};
 $('#hh').onchange=draw;draw()}
</script></body></html>
"""
