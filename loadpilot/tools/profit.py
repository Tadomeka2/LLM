"""Profit calculation and load ranking tools."""

from __future__ import annotations

from .. import config
from ..data_source import get_load, load_board
from ..errors import ToolError
from ..filters import available_states, filter_loads, load_summary, normalize_trailer, parse_iso_date
from ..models import compute_profit, compute_profit_values
from ..places import lookup_city, normalize_state, road_distance
from .registry import tool


def _validate_cost_inputs(mpg: float, diesel_price: float, fixed_cpm: float, target_rpm: float, deadhead: float) -> None:
    if mpg <= 0:
        raise ToolError("mpg must be greater than 0")
    if diesel_price < 0:
        raise ToolError("diesel_price cannot be negative")
    if fixed_cpm < 0:
        raise ToolError("fixed_cost_per_mile cannot be negative")
    if target_rpm < 0:
        raise ToolError("target_effective_rpm cannot be negative")
    if deadhead < 0:
        raise ToolError("deadhead_miles cannot be negative")


@tool(
    name="calculate_profit",
    description=(
        "Calculate net profit, break-even rate and effective RPM for one load, "
        "including deadhead miles, fuel and fixed cost per mile. Use load_id "
        "from the board, or pass rate_usd + loaded_miles for a hypothetical load."
    ),
    parameters={
        "type": "object",
        "properties": {
            "load_id": {"type": "string", "description": "Load ID from the board, e.g. SD-1004."},
            "rate_usd": {"type": "number", "description": "Gross rate for a hypothetical load."},
            "loaded_miles": {"type": "integer", "description": "Loaded miles for a hypothetical load."},
            "deadhead_miles": {"type": "number", "description": "Empty miles to reach the pickup (default 0)."},
            "mpg": {"type": "number", "description": "Truck fuel economy (default 6.5)."},
            "diesel_price": {"type": "number", "description": "Diesel price per gallon (default 3.75)."},
            "fixed_cost_per_mile": {"type": "number", "description": "Non-fuel cost per mile (default 0.35)."},
            "target_effective_rpm": {"type": "number", "description": "Minimum acceptable net USD per total mile (default 0.45)."},
        },
        "required": [],
    },
)
def calculate_profit(
    load_id: str | None = None,
    rate_usd: float | None = None,
    loaded_miles: int | None = None,
    deadhead_miles: float = 0.0,
    mpg: float = config.DEFAULT_MPG,
    diesel_price: float = config.DEFAULT_DIESEL_PRICE,
    fixed_cost_per_mile: float = config.DEFAULT_FIXED_CPM,
    target_effective_rpm: float = config.DEFAULT_TARGET_RPM,
) -> dict:
    """Calculate a complete per-load profit and loss statement.

    Name:
        calculate_profit

    Description:
        Applies fuel and fixed costs to loaded + deadhead miles and returns
        net profit, effective RPM, break-even rate and a verdict. Either pass
        a real ``load_id`` or a hypothetical ``rate_usd`` + ``loaded_miles``.
        The LLM never performs this arithmetic itself.

    Parameters:
        load_id (str, optional): id of a load on the board.
        rate_usd (float, optional): gross rate for a hypothetical load.
        loaded_miles (int, optional): loaded miles for a hypothetical load.
        deadhead_miles (float): empty miles to pickup, default 0.
        mpg (float): fuel economy, default 6.5.
        diesel_price (float): diesel USD/gallon, default 3.75.
        fixed_cost_per_mile (float): non-fuel cost per mile, default 0.35.
        target_effective_rpm (float): target net USD per total mile, default 0.45.

    Returns:
        dict: ``{ok, load_id, lane, rate_usd, total_miles, fuel_cost,
        fixed_cost, net_profit, effective_rpm, break_even_rate, verdict,
        verdict_reason, assumptions}``. Invalid inputs or unknown IDs return
        ``{ok: false, error, hint}``.
    """

    _validate_cost_inputs(mpg, diesel_price, fixed_cost_per_mile, target_effective_rpm, deadhead_miles)

    if load_id and (rate_usd is not None or loaded_miles is not None):
        raise ToolError(
            "provide either load_id or rate_usd + loaded_miles, not both",
            hint="Use load_id for a posted load; use rate/miles only for a hypothetical one.",
        )
    if not load_id and (rate_usd is None or loaded_miles is None):
        raise ToolError(
            "missing load data",
            hint="Provide load_id, or both rate_usd and loaded_miles.",
        )
    if rate_usd is not None and rate_usd < 0:
        raise ToolError("rate_usd cannot be negative")
    if loaded_miles is not None and loaded_miles <= 0:
        raise ToolError("loaded_miles must be greater than 0")

    if load_id:
        load = get_load(load_id)
        result = compute_profit(
            load,
            deadhead_miles=deadhead_miles,
            mpg=mpg,
            diesel_price=diesel_price,
            fixed_cost_per_mile=fixed_cost_per_mile,
            target_effective_rpm=target_effective_rpm,
        )
        result["source"] = load_board().source
    else:
        result = compute_profit_values(
            float(rate_usd),
            int(loaded_miles),
            deadhead_miles=deadhead_miles,
            mpg=mpg,
            diesel_price=diesel_price,
            fixed_cost_per_mile=fixed_cost_per_mile,
            target_effective_rpm=target_effective_rpm,
        )
    return {"ok": True, **result}


@tool(
    name="rank_loads",
    description=(
        "Rank filtered loads by real profitability (net profit or effective "
        "RPM), automatically adding deadhead from the driver's city. This is "
        "the correct tool for 'which load is best/most profitable' questions."
    ),
    parameters={
        "type": "object",
        "properties": {
            "driver_city": {"type": "string", "description": "Driver's current city and state, e.g. 'Dallas, TX'."},
            "origin_state": {"type": "string", "description": "Two-letter origin state filter."},
            "dest_state": {"type": "string", "description": "Two-letter destination state filter."},
            "pickup_after": {"type": "string", "description": "Earliest pickup date, YYYY-MM-DD."},
            "trailer_type": {"type": "string", "enum": ["open", "enclosed"], "description": "Trailer type filter."},
            "min_rate": {"type": "number", "description": "Minimum gross rate in USD."},
            "limit": {"type": "integer", "description": "Number of ranked loads to return (1-25, default 5)."},
            "sort_by": {
                "type": "string",
                "enum": ["net_profit", "effective_rpm", "gross_rpm"],
                "description": "Ranking metric, default net_profit.",
            },
            "mpg": {"type": "number", "description": "Truck fuel economy (default 6.5)."},
            "diesel_price": {"type": "number", "description": "Diesel price per gallon (default 3.75)."},
            "fixed_cost_per_mile": {"type": "number", "description": "Non-fuel cost per mile (default 0.35)."},
            "target_effective_rpm": {"type": "number", "description": "Target net USD per total mile (default 0.45)."},
        },
        "required": [],
    },
)
def rank_loads(
    driver_city: str | None = None,
    origin_state: str | None = None,
    dest_state: str | None = None,
    pickup_after: str | None = None,
    trailer_type: str | None = None,
    min_rate: float | None = None,
    limit: int = 5,
    sort_by: str = "net_profit",
    mpg: float = config.DEFAULT_MPG,
    diesel_price: float = config.DEFAULT_DIESEL_PRICE,
    fixed_cost_per_mile: float = config.DEFAULT_FIXED_CPM,
    target_effective_rpm: float = config.DEFAULT_TARGET_RPM,
) -> dict:
    """Rank loads by profitability after deadhead and operating costs.

    Name:
        rank_loads

    Description:
        Filters the board, estimates deadhead from ``driver_city`` to each
        origin using the offline city table, computes net profit and effective
        RPM, then returns the top N. Without ``driver_city`` deadhead is
        assumed to be zero and the response includes a warning.

    Parameters:
        driver_city (str, optional): current city/state, e.g. "Dallas, TX".
        origin_state (str, optional): two-letter origin state.
        dest_state (str, optional): two-letter destination state.
        pickup_after (str, optional): ISO date, inclusive.
        trailer_type (str, optional): "open" or "enclosed".
        min_rate (float, optional): minimum gross rate.
        limit (int): rows to return, 1-25, default 5.
        sort_by (str): "net_profit", "effective_rpm" or "gross_rpm".
        mpg (float): fuel economy, default 6.5.
        diesel_price (float): diesel USD/gallon, default 3.75.
        fixed_cost_per_mile (float): non-fuel cost per mile, default 0.35.
        target_effective_rpm (float): target net USD per total mile, default 0.45.

    Returns:
        dict: ``{ok, source, driver, matched_count, returned, warning, loads}``
        where each load includes rank, id, lane, rate, deadhead_miles,
        total_miles, net_profit, effective_rpm and verdict.
    """

    _validate_cost_inputs(mpg, diesel_price, fixed_cost_per_mile, target_effective_rpm, 0)
    if limit < 1:
        raise ToolError("limit must be at least 1")
    if limit > 25:
        limit = 25

    origin = normalize_state(origin_state) if origin_state else None
    dest = normalize_state(dest_state) if dest_state else None
    pickup = parse_iso_date(pickup_after) if pickup_after else None
    trailer = normalize_trailer(trailer_type) if trailer_type else None

    driver = None
    if driver_city:
        driver = lookup_city(driver_city)
        driver_label = f"{driver['city']}, {driver['state']}"
    else:
        driver_label = None

    board = load_board()
    matches = filter_loads(
        board.loads,
        origin_state=origin,
        dest_state=dest,
        pickup_after=pickup,
        trailer_type=trailer,
        min_rate=min_rate,
    )
    if not matches:
        states = available_states(board.loads)
        return {
            "ok": True,
            **board.to_meta(),
            "driver": driver_label,
            "matched_count": 0,
            "returned": 0,
            "loads": [],
            "hint": (
                "No loads matched those filters. Try removing a filter. "
                f"States present on this board: {', '.join(states[:15])}."
            ),
        }

    warning = None
    if driver:
        deadheads = {
            load.id: road_distance(
                f"{driver['city']}, {driver['state']}",
                f"{load.origin_city}, {load.origin_state}",
            )["miles"]
            for load in matches
        }
    else:
        deadheads = {load.id: 0 for load in matches}
        warning = "driver_city was not provided, so deadhead miles were assumed to be 0."

    ranked = []
    for load in matches:
        profit = compute_profit(
            load,
            deadhead_miles=deadheads[load.id],
            mpg=mpg,
            diesel_price=diesel_price,
            fixed_cost_per_mile=fixed_cost_per_mile,
            target_effective_rpm=target_effective_rpm,
        )
        ranked.append({**load_summary(load, include_notes=False), **profit})

    metric = {
        "net_profit": lambda item: item["net_profit"],
        "effective_rpm": lambda item: item["effective_rpm"],
        "gross_rpm": lambda item: item["gross_rpm"],
    }[sort_by]
    ranked.sort(key=metric, reverse=True)
    top = ranked[:limit]
    for index, item in enumerate(top, start=1):
        item["rank"] = index

    return {
        "ok": True,
        **board.to_meta(),
        "driver": driver_label,
        "sort_by": sort_by,
        "matched_count": len(matches),
        "returned": len(top),
        "deadhead_warning": warning,
        "loads": top,
        "assumptions": {
            "mpg": mpg,
            "diesel_price": diesel_price,
            "fixed_cost_per_mile": fixed_cost_per_mile,
            "target_effective_rpm": target_effective_rpm,
        },
    }
