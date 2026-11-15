"""Bot 4: Valuation Analyst. A great company at a silly price is a bad investment.

Compares today's PE with (a) the company's own PE over the past 5 years and
(b) the median PE of same-sector companies, and checks PEG (PE divided by
profit growth in %). Rejects when the price is not justified.

PE here = market value / last published full-year profit.
"""
from __future__ import annotations

from ..config import Config


def run(view, analyst: dict, peer_pe: float | None, cfg: Config) -> dict:
    m = analyst.get("metrics", {})
    pe = view.pe
    hist = view.pe_history
    hist = hist[(hist > 0) & (hist < 500)]
    res = {"pe": pe, "pe_median": float(hist.median()) if len(hist) >= 12 else None,
           "pe_months": int(len(hist)), "peer_pe": peer_pe, "peg": None, "ok": False, "score": 0, "reasons": []}
    if pe is None:
        res["reasons"].append("No profit to value it on.")
        return res
    g = m.get("pat_cagr")
    if g is not None and g > 0:
        res["peg"] = float(pe / (g * 100))
    bad = res["reasons"]
    if res["peg"] is None:
        bad.append("Profits are not growing, so no price can be called fair for a growth holding.")
    elif res["peg"] > cfg.peg_max:
        bad.append(f"PEG is {res['peg']:.1f} (above {cfg.peg_max:.0f}): paying too much for the growth.")
    if res["pe_median"] and pe > cfg.pe_vs_own_history_max * res["pe_median"]:
        bad.append(f"PE {pe:.0f} is well above its own usual {res['pe_median']:.0f}.")
    if peer_pe and pe > cfg.pe_vs_peers_max * peer_pe and (res["peg"] is None or res["peg"] > 1.5):
        bad.append(f"PE {pe:.0f} is far above sector peers at {peer_pe:.0f} without the growth to match.")
    if res["pe_median"] is None and peer_pe is None:
        bad.append("Nothing to compare the price against (no PE history and no peers).")
    res["ok"] = not bad
    # 0-100: cheaper against history, peers and growth = higher
    pts, n = 0.0, 0
    for ref in (res["pe_median"], peer_pe):
        if ref:
            pts += max(0.0, min(1.0, 1.5 - pe / ref)); n += 1
    if res["peg"]:
        pts += max(0.0, min(1.0, (2.5 - res["peg"]) / 2)); n += 1
    res["score"] = round(100 * pts / n) if n else 0
    if res["ok"]:
        bits = [f"PE {pe:.0f}"]
        if res["pe_median"]:
            bits.append(f"own 5-year median {res['pe_median']:.0f}")
        if peer_pe:
            bits.append(f"peers {peer_pe:.0f}")
        bits.append(f"PEG {res['peg']:.1f}")
        res["reasons"].append("Price looks reasonable: " + ", ".join(bits) + ".")
    return res
