"""Read-only link to Zerodha through the official Kite MCP server (https://mcp.kite.trade/mcp).

This module can call exactly two tools on that server: `login` and `get_holdings`.
The allowlist below is checked before every call. Order tools (place_order,
modify_order, cancel_order, place_gtt_order, modify_gtt_order, delete_gtt_order)
are named in a blocklist as a second lock. Nothing here can trade.

Your password never passes through this program: `login` returns a Zerodha web
link, you sign in in your own browser, and the session is read-only from our side.
"""
from __future__ import annotations

import asyncio
import csv
import json
import re
from datetime import datetime
from pathlib import Path

KITE_MCP_URL = "https://mcp.kite.trade/mcp"
ALLOWED_TOOLS = frozenset({"login", "get_holdings"})
FORBIDDEN_WORDS = ("order", "gtt", "place", "modify", "cancel", "delete")


def guard(tool: str) -> str:
    if tool not in ALLOWED_TOOLS or any(w in tool.lower() for w in FORBIDDEN_WORDS):
        raise PermissionError(f"Blocked: '{tool}' is not a read-only holdings tool. This project never places orders.")
    return tool


def _text(result) -> str:
    return "\n".join(getattr(c, "text", "") for c in getattr(result, "content", []) if getattr(c, "text", None))


def normalise(rows: list[dict]) -> list[dict]:
    out = []
    for r in rows:
        sym = r.get("tradingsymbol") or r.get("symbol")
        if not sym:
            continue
        qty = float(r.get("quantity", 0) or 0) + float(r.get("t1_quantity", 0) or 0)
        out.append({"symbol": str(sym).upper().replace("-EQ", ""), "quantity": qty,
                    "average_price": float(r.get("average_price", 0) or 0),
                    "last_price": float(r.get("last_price", 0) or 0)})
    return [h for h in out if h["quantity"] > 0]


def save(holdings: list[dict], data_dir: Path, source: str) -> Path:
    p = Path(data_dir) / "holdings.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"synced_at": datetime.now().isoformat(timespec="minutes"), "source": source,
                             "holdings": holdings}, indent=1))
    return p


async def _sync(data_dir: Path) -> Path:
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client
    async with streamablehttp_client(KITE_MCP_URL) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            msg = _text(await session.call_tool(guard("login"), {}))
            link = re.search(r"https://\S+", msg)
            print("\nOpen this link in your browser and sign in to Zerodha:\n")
            print("  " + (link.group(0).rstrip(").,") if link else msg))
            input("\nPress Enter here after you have signed in... ")
            raw = _text(await session.call_tool(guard("get_holdings"), {}))
            data = json.loads(raw)
            rows = data.get("data", data) if isinstance(data, dict) else data
            return save(normalise(rows), data_dir, "kite-mcp (read-only)")


def sync_from_kite(data_dir: Path) -> Path:
    return asyncio.run(_sync(data_dir))


def import_csv(path: Path, data_dir: Path) -> Path:
    """Fallback: Zerodha Console > Holdings > Download. No login from this program at all."""
    with open(path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.reader(f))
    head_i = next(i for i, r in enumerate(rows) if any("symbol" in c.lower() or "instrument" in c.lower() for c in r))
    head = [c.strip().lower() for c in rows[head_i]]

    def col(*words):
        return next((i for i, c in enumerate(head) if any(w in c for w in words)), None)

    c_sym, c_qty = col("symbol", "instrument"), col("quantity available", "qty", "quantity")
    c_avg, c_ltp = col("average", "avg"), col("ltp", "closing", "previous", "last")
    out = []
    for r in rows[head_i + 1:]:
        try:
            out.append({"tradingsymbol": r[c_sym].strip(), "quantity": float(r[c_qty].replace(",", "")),
                        "average_price": float(r[c_avg].replace(",", "")) if c_avg is not None else 0,
                        "last_price": float(r[c_ltp].replace(",", "")) if c_ltp is not None else 0})
        except (ValueError, IndexError, TypeError):
            continue
    return save(normalise(out), data_dir, f"csv:{Path(path).name}")
