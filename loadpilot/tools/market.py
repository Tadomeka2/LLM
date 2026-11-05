"""Lane market statistics tool."""

from __future__ import annotations

import statistics
from collections import defaultdict

from ..data_source import load_board
from ..errors import ToolError
from ..filters import available_states, filter_loads, normalize_trailer
from ..places import normalize_state
from .registry import tool


@tool(
    name="market_stats",
    description=(
        "Summarize the current board for a lane or state pair: number of loads, "
        "average/median gross rate and rate per mile. Use it to judge whether a "
        "specific offer is above or below the market."
    ),
    parameters={
        "type": "object",
        "properties": {
            "origin_state": {"type": "string", "description": "Two-letter origin state, e.g. TX."},
            "dest_state": {"type": "string", "description": "Two-letter destination state, e.g. GA."},
            "trailer_type": {"type": "string", "enum": ["open", "enclosed"], "description": "Trailer type filter."},
            "min_loads": {"type": "integer", "description": "Minimum sample size for a lane to appear in best_lanes (default 2)."},
        },
        "required": [],
    },
)
def market_stats(
    origin_state: str | None = None,
    dest_state: str | None = None,
    trailer_type: str | None = None,
    min_loads: int = 2,
) -> dict:
    """Summarize rates for a lane.

    Name:
        market_stats

    Description:
        Computes descriptive statistics over the currently loaded board. It
        describes the sample, not the whole US market; the response includes
        the sample size and data source so the agent can qualify its answer.

    Parameters:
        origin_state (str, optional): two-letter origin state.
        dest_state (str, optional): two-letter destination state.
        trailer_type (str, optional): "open" or "enclosed".
        min_loads (int): minimum lane sample size, default 2.

    Returns:
        dict: ``{ok, source, filters, count, avg_rate, median_rate,
        avg_gross_rpm, median_gross_rpm, min_rate, max_rate,
        avg_loaded_miles, best_lanes}``. Empty samples return a hint instead
        of fabricated numbers.
    """

    if min_loads < 1:
        raise ToolError("min_loads must be at least 1")
    min_loads = min(min_loads, 10)

    origin = normalize_state(origin_state) if origin_state else None
    dest = normalize_state(dest_state) if dest_state else None
    trailer = normalize_trailer(trailer_type) if trailer_type else None

    board = load_board()
    matches = filter_loads(
        board.loads,
        origin_state=origin,
        dest_state=dest,
        trailer_type=trailer,
    )
    filters = {"origin_state": origin, "dest_state": dest, "trailer_type": trailer}

    if not matches:
        states = available_states(board.loads)
        return {
            "ok": True,
            **board.to_meta(),
            "filters": filters,
            "count": 0,
            "hint": (
                "No loads matched, so there is no sample to summarize. "
                f"States present on this board: {', '.join(states[:15])}."
            ),
        }

    rates = [load.rate_usd for load in matches]
    rpms = [load.gross_rpm for load in matches]
    miles = [load.loaded_miles for load in matches]

    lanes: dict[str, list[float]] = defaultdict(list)
    for load in matches:
        lanes[load.lane].append(load.gross_rpm)
    lane_stats = [
        {
            "lane": lane,
            "loads": len(values),
            "avg_gross_rpm": round(statistics.mean(values), 3),
            "avg_rate": round(
                statistics.mean(load.rate_usd for load in matches if load.lane == lane), 2
            ),
        }
        for lane, values in lanes.items()
        if len(values) >= min_loads
    ]
    lane_stats.sort(key=lambda item: item["avg_gross_rpm"], reverse=True)

    return {
        "ok": True,
        **board.to_meta(),
        "filters": filters,
        "count": len(matches),
        "avg_rate": round(statistics.mean(rates), 2),
        "median_rate": round(statistics.median(rates), 2),
        "avg_gross_rpm": round(statistics.mean(rpms), 3),
        "median_gross_rpm": round(statistics.median(rpms), 3),
        "min_rate": round(min(rates), 2),
        "max_rate": round(max(rates), 2),
        "avg_loaded_miles": round(statistics.mean(miles), 0),
        "best_lanes": lane_stats[:3],
        "note": "Descriptive statistics for the current board sample only; not a market forecast.",
    }
