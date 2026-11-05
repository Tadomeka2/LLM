"""Filtering and compact serialization shared by the tools."""

from __future__ import annotations

from datetime import date

from .errors import ToolError
from .models import Load


def parse_iso_date(value: str, field: str = "pickup_after") -> str:
    text = str(value).strip()
    try:
        return date.fromisoformat(text[:10]).isoformat()
    except ValueError as exc:
        raise ToolError(
            f"{field} must be an ISO date (YYYY-MM-DD), got '{value}'",
            hint="Example: 2026-11-05.",
        ) from exc


def normalize_trailer(value: str) -> str:
    text = str(value).strip().lower()
    aliases = {
        "open": "open",
        "open trailer": "open",
        "flatbed": "open",
        "enclosed": "enclosed",
        "closed": "enclosed",
        "enclosed trailer": "enclosed",
    }
    if text not in aliases:
        raise ToolError(
            f"unknown trailer type '{value}'",
            hint="Use 'open' or 'enclosed'.",
        )
    return aliases[text]


def filter_loads(
    loads: list[Load],
    *,
    origin_state: str | None = None,
    dest_state: str | None = None,
    pickup_after: str | None = None,
    trailer_type: str | None = None,
    min_rate: float | None = None,
    max_rate: float | None = None,
) -> list[Load]:
    result = list(loads)
    if origin_state:
        result = [load for load in result if load.origin_state == origin_state]
    if dest_state:
        result = [load for load in result if load.dest_state == dest_state]
    if pickup_after:
        result = [load for load in result if load.pickup_date >= pickup_after]
    if trailer_type:
        result = [load for load in result if load.trailer_type == trailer_type]
    if min_rate is not None:
        result = [load for load in result if load.rate_usd >= min_rate]
    if max_rate is not None:
        result = [load for load in result if load.rate_usd <= max_rate]
    return result


def load_summary(load: Load, *, include_notes: bool = True) -> dict:
    data = {
        "id": load.id,
        "broker": load.broker,
        "lane": load.lane,
        "origin": f"{load.origin_city}, {load.origin_state}",
        "destination": f"{load.dest_city}, {load.dest_state}",
        "pickup_date": load.pickup_date,
        "delivery_date": load.delivery_date,
        "vehicles": load.vehicles,
        "rate_usd": round(load.rate_usd, 2),
        "loaded_miles": load.loaded_miles,
        "gross_rpm": round(load.gross_rpm, 3),
        "trailer_type": load.trailer_type,
        "vehicle_type": load.vehicle_type,
    }
    if include_notes and load.notes:
        data["notes"] = load.notes[:120]
    return data


def available_states(loads: list[Load]) -> list[str]:
    return sorted({load.origin_state for load in loads} | {load.dest_state for load in loads})
