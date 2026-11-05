"""US place parsing and an offline road-distance estimate.

The distance tool is intentionally offline: it must work during a demo with
no network. It is a great-circle distance multiplied by a road factor, not a
truck routing engine, and the output says so.
"""

from __future__ import annotations

import difflib
import json
import math
from functools import lru_cache

from .config import CITIES_PATH
from .errors import ToolError

STATE_NAMES = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR",
    "california": "CA", "colorado": "CO", "connecticut": "CT", "delaware": "DE",
    "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID",
    "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS",
    "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD",
    "massachusetts": "MA", "michigan": "MI", "minnesota": "MN", "mississippi": "MS",
    "missouri": "MO", "montana": "MT", "nebraska": "NE", "nevada": "NV",
    "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM", "new york": "NY",
    "north carolina": "NC", "north dakota": "ND", "ohio": "OH", "oklahoma": "OK",
    "oregon": "OR", "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC",
    "south dakota": "SD", "tennessee": "TN", "texas": "TX", "utah": "UT",
    "vermont": "VT", "virginia": "VA", "washington": "WA", "west virginia": "WV",
    "wisconsin": "WI", "wyoming": "WY", "district of columbia": "DC",
}
STATE_CODES = set(STATE_NAMES.values())


def normalize_state(value: str) -> str:
    text = str(value).strip()
    if len(text) == 2 and text.isalpha():
        code = text.upper()
        if code in STATE_CODES:
            return code
    code = STATE_NAMES.get(text.lower().replace(".", "").strip())
    if code:
        return code
    raise ToolError(
        f"'{value}' is not a US state code",
        hint="Use a two-letter code such as TX, GA, CA, WA.",
    )


@lru_cache(maxsize=1)
def _cities() -> dict[str, dict]:
    with CITIES_PATH.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def split_place(text: str) -> tuple[str, str | None]:
    """Split 'Dallas, TX' / 'Dallas TX' / 'Dallas, Texas' into city, state."""

    value = str(text).strip()
    if not value:
        raise ToolError("place is empty", hint="Use a format like 'Dallas, TX'.")
    if "," in value:
        city, state = value.rsplit(",", 1)
        city, state = city.strip(), state.strip()
    else:
        parts = value.split()
        if len(parts) >= 2 and (parts[-1].upper() in STATE_CODES or parts[-1].lower() in STATE_NAMES):
            city, state = " ".join(parts[:-1]), parts[-1]
        else:
            city, state = value, None
    if not city:
        raise ToolError(f"could not read a city from '{text}'", hint="Use 'Dallas, TX'.")
    return city, normalize_state(state) if state else None


def lookup_city(text: str) -> dict:
    city, state = split_place(text)
    data = _cities()
    if state:
        key = f"{city.lower()}|{state}"
        if key in data:
            return data[key]
        in_state = sorted(item["city"] for key_, item in data.items() if item["state"] == state)
        suggestions = difflib.get_close_matches(city, in_state, n=3, cutoff=0.5)
        hint = f"Known cities in {state}: {', '.join(in_state[:12])}."
        if suggestions:
            hint = f"Did you mean: {', '.join(f'{s}, {state}' for s in suggestions)}? " + hint
        raise ToolError(f"unknown city '{city}, {state}' in the offline city table", hint=hint)

    matches = [item for item in data.values() if item["city"].lower() == city.lower()]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        options = ", ".join(f"{m['city']}, {m['state']}" for m in matches)
        raise ToolError(
            f"city '{city}' exists in several states",
            hint=f"Add a state: {options}.",
        )
    all_cities = sorted({item["city"] for item in data.values()})
    suggestions = difflib.get_close_matches(city, all_cities, n=3, cutoff=0.5)
    hint = "Use a major US city from data/cities.json, e.g. 'Dallas, TX'."
    if suggestions:
        hint = f"Did you mean: {', '.join(suggestions)}? " + hint
    raise ToolError(f"unknown city '{city}'", hint=hint)


def _haversine_miles(a: dict, b: dict) -> float:
    radius = 3958.8
    lat1, lon1 = math.radians(a["lat"]), math.radians(a["lon"])
    lat2, lon2 = math.radians(b["lat"]), math.radians(b["lon"])
    dlat, dlon = lat2 - lat1, lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * radius * math.asin(math.sqrt(h))


def road_distance(origin: str, destination: str) -> dict:
    """Estimate road miles between two 'City, ST' strings."""

    start = lookup_city(origin)
    end = lookup_city(destination)
    straight = _haversine_miles(start, end)
    miles = int(round(straight * 1.18 / 10.0) * 10)
    if start["city"] == end["city"] and start["state"] == end["state"]:
        miles = 0
    return {
        "origin": f"{start['city']}, {start['state']}",
        "destination": f"{end['city']}, {end['state']}",
        "miles": miles,
        "drive_hours": round(miles / 55.0, 1),
        "method": "great-circle distance x 1.18 road factor, 55 mph average",
        "disclaimer": "Estimate only - not a truck-routed distance; check a routing app before bidding.",
    }
