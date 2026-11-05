"""Offline route-distance tool."""

from __future__ import annotations

from ..places import road_distance
from .registry import tool


@tool(
    name="route_distance",
    description=(
        "Estimate road miles and driving hours between two US cities using an "
        "offline city table. Use it for deadhead and total-mile calculations. "
        "It is an estimate, not a truck routing engine."
    ),
    parameters={
        "type": "object",
        "properties": {
            "origin": {"type": "string", "description": "City and state, e.g. 'Dallas, TX'."},
            "destination": {"type": "string", "description": "City and state, e.g. 'Atlanta, GA'."},
        },
        "required": ["origin", "destination"],
    },
)
def route_distance(origin: str, destination: str) -> dict:
    """Estimate the distance between two US cities.

    Name:
        route_distance

    Description:
        Uses a bundled table of major US cities and the haversine formula
        multiplied by a 1.18 road factor. Works fully offline and never calls
        a mapping API. The result is an estimate for planning, not a legal
        truck route.

    Parameters:
        origin (str): city and state, e.g. "Dallas, TX".
        destination (str): city and state, e.g. "Atlanta, GA".

    Returns:
        dict: ``{ok, origin, destination, miles, drive_hours, method,
        disclaimer}``. Unknown cities return ``{ok: false, error, hint}`` with
        close matches.
    """

    result = road_distance(origin, destination)
    return {"ok": True, **result}
