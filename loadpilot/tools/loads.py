"""Load search tool."""

from __future__ import annotations

from ..data_source import load_board
from ..errors import ToolError
from ..filters import available_states, filter_loads, load_summary, normalize_trailer, parse_iso_date
from ..places import normalize_state
from .registry import tool


@tool(
    name="search_loads",
    description=(
        "Search the SuperDispatch load board for posted auto-transport loads. "
        "Filter by origin/destination state, pickup date, trailer type and rate. "
        "Use this before making any claim about which loads exist."
    ),
    parameters={
        "type": "object",
        "properties": {
            "origin_state": {"type": "string", "description": "Two-letter US state code, e.g. TX."},
            "dest_state": {"type": "string", "description": "Two-letter US state code, e.g. GA."},
            "pickup_after": {"type": "string", "description": "Earliest pickup date, YYYY-MM-DD (inclusive)."},
            "trailer_type": {"type": "string", "enum": ["open", "enclosed"], "description": "Trailer type."},
            "min_rate": {"type": "number", "description": "Minimum gross rate in USD."},
            "max_rate": {"type": "number", "description": "Maximum gross rate in USD."},
            "limit": {"type": "integer", "description": "Maximum rows to return (1-25, default 5)."},
        },
        "required": [],
    },
)
def search_loads(
    origin_state: str | None = None,
    dest_state: str | None = None,
    pickup_after: str | None = None,
    trailer_type: str | None = None,
    min_rate: float | None = None,
    max_rate: float | None = None,
    limit: int = 5,
) -> dict:
    """Search posted loads.

    Name:
        search_loads

    Description:
        Returns loads currently on the board that match the filters. Results
        are sorted by earliest pickup, then highest rate. The response always
        includes ``source`` (demo/file/api) so the agent can disclose whether
        the data is live.

    Parameters:
        origin_state (str, optional): two-letter US state code.
        dest_state (str, optional): two-letter US state code.
        pickup_after (str, optional): ISO date, inclusive.
        trailer_type (str, optional): "open" or "enclosed".
        min_rate (float, optional): minimum gross rate in USD.
        max_rate (float, optional): maximum gross rate in USD.
        limit (int): 1-25, default 5.

    Returns:
        dict: ``{ok, source, warning, fetched_at, count, loads, hint?}`` where
        each load contains id, lane, pickup_date, vehicles, rate_usd,
        loaded_miles, gross_rpm, trailer_type and broker.
    """

    origin = normalize_state(origin_state) if origin_state else None
    dest = normalize_state(dest_state) if dest_state else None
    pickup = parse_iso_date(pickup_after) if pickup_after else None
    trailer = normalize_trailer(trailer_type) if trailer_type else None

    if min_rate is not None and min_rate < 0:
        raise ToolError("min_rate cannot be negative")
    if max_rate is not None and max_rate < 0:
        raise ToolError("max_rate cannot be negative")
    if min_rate is not None and max_rate is not None and min_rate > max_rate:
        raise ToolError("min_rate cannot be greater than max_rate")

    if limit < 1:
        raise ToolError("limit must be at least 1")
    limit_note = None
    if limit > 25:
        limit = 25
        limit_note = "limit was capped at 25 to keep the model context small."

    board = load_board()
    matches = filter_loads(
        board.loads,
        origin_state=origin,
        dest_state=dest,
        pickup_after=pickup,
        trailer_type=trailer,
        min_rate=min_rate,
        max_rate=max_rate,
    )
    matches.sort(key=lambda load: (load.pickup_date, -load.rate_usd))

    payload = {
        "ok": True,
        **board.to_meta(),
        "filters": {
            "origin_state": origin,
            "dest_state": dest,
            "pickup_after": pickup,
            "trailer_type": trailer,
            "min_rate": min_rate,
            "max_rate": max_rate,
        },
        "count": len(matches),
        "returned": min(len(matches), limit),
        "loads": [load_summary(load) for load in matches[:limit]],
    }
    if not matches:
        states = available_states(board.loads)
        payload["hint"] = (
            "No loads matched. Try removing the state/trailer/date filters. "
            f"States present on this board: {', '.join(states[:15])}."
        )
    if limit_note:
        payload["note"] = limit_note
    return payload
