"""Offline smoke test for every tool (no model required).

Run:
    python scripts/tool_smoke.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Use a throwaway watchlist so the real one is never modified by this script.
_temp = tempfile.NamedTemporaryFile(prefix="loadpilot-watchlist-", suffix=".json", delete=False)
_temp.close()
os.environ["WATCHLIST_FILE"] = _temp.name

from loadpilot.tools import call_tool, describe_tools  # noqa: E402


def show(label: str, result: dict) -> dict:
    print(f"\n=== {label} ===")
    print(json.dumps(result, ensure_ascii=False, indent=2)[:1400])
    return result


def main() -> int:
    print("registered tools:", ", ".join(tool["name"] for tool in describe_tools()))

    found = show(
        "search_loads TX->GA open",
        call_tool(
            "search_loads",
            {"origin_state": "TX", "dest_state": "GA", "trailer_type": "open", "pickup_after": "2026-11-05"},
        ),
    )
    assert found["ok"] and found["count"] >= 3

    ranked = show(
        "rank_loads from Dallas",
        call_tool("rank_loads", {"driver_city": "Dallas, TX", "origin_state": "TX", "dest_state": "GA", "limit": 3}),
    )
    assert ranked["ok"] and ranked["loads"][0]["rank"] == 1

    profit = show(
        "calculate_profit SD-1004",
        call_tool("calculate_profit", {"load_id": "SD-1004", "deadhead_miles": 120}),
    )
    assert profit["ok"] and profit["total_miles"] == 900

    distance = show(
        "route_distance Dallas -> Atlanta",
        call_tool("route_distance", {"origin": "Dallas, TX", "destination": "Atlanta, GA"}),
    )
    assert distance["ok"] and 700 <= distance["miles"] <= 900

    stats = show(
        "market_stats TX->GA",
        call_tool("market_stats", {"origin_state": "TX", "dest_state": "GA"}),
    )
    assert stats["ok"] and stats["count"] >= 3

    added = show(
        "watchlist add",
        call_tool("watchlist", {"action": "add", "load_id": "SD-1004", "note": "call broker tomorrow"}),
    )
    assert added["ok"] and added["entry"]["load_id"] == "SD-1004"

    listed = show("watchlist list", call_tool("watchlist", {"action": "list"}))
    assert listed["count"] == 1

    removed = show("watchlist remove", call_tool("watchlist", {"action": "remove", "load_id": "SD-1004"}))
    assert removed["removed"]

    bad_state = show("error: invalid state", call_tool("search_loads", {"origin_state": "ZZ"}))
    assert bad_state["ok"] is False and "hint" in bad_state

    bad_id = show("error: unknown load", call_tool("calculate_profit", {"load_id": "SD-9999"}))
    assert bad_id["ok"] is False and "hint" in bad_id

    print("\nAll tool smoke tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
