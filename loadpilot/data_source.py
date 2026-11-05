"""Load board providers.

Priority:
1. Live SuperDispatch API (only when both URL and token are configured).
2. A local JSON/CSV export (``data/loads.json`` by default).
3. The bundled demo board, clearly labelled as ``source="demo"``.

The agent must never pretend demo rows are live loads, so the source is part
of every tool result and is mentioned in the README/system prompt.
"""

from __future__ import annotations

import csv
import difflib
import json
import logging
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import config
from .errors import ToolError
from .models import Load
from .places import split_place

log = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class LoadBoard:
    loads: list[Load]
    source: str
    warning: str | None = None
    fetched_at: str = field(default_factory=_now)

    def to_meta(self) -> dict:
        return {
            "source": self.source,
            "warning": self.warning,
            "fetched_at": self.fetched_at,
            "total_on_board": len(self.loads),
        }


def _extract_records(payload: Any) -> list[dict]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("loads", "data", "results", "items", "orders"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    raise ToolError(
        "the load source did not return a list of loads",
        hint="Expected a JSON array or an object with a 'loads'/'data'/'results' array.",
    )


def _nested_place(raw: dict, prefix: str) -> tuple[str | None, str | None]:
    city = raw.get(f"{prefix}_city")
    state = raw.get(f"{prefix}_state")
    nested = raw.get(prefix)
    if isinstance(nested, dict):
        city = city or nested.get("city") or nested.get("name")
        state = state or nested.get("state") or nested.get("state_code")
    elif isinstance(nested, str) and not (city and state):
        try:
            parsed_city, parsed_state = split_place(nested)
            city = city or parsed_city
            state = state or parsed_state
        except ToolError:
            pass
    if isinstance(city, str) and "," in city and not state:
        try:
            parsed_city, parsed_state = split_place(city)
            city, state = parsed_city, parsed_state
        except ToolError:
            pass
    return (str(city).strip() if city else None, str(state).strip() if state else None)


def _number(value: Any) -> Any:
    if isinstance(value, dict):
        for key in ("amount", "total", "value"):
            if key in value:
                return value[key]
    return value


def normalize_record(raw: dict) -> Load:
    """Map common API/CSV field names onto :class:`Load`."""

    origin_city, origin_state = _nested_place(raw, "origin")
    if not origin_city:
        origin_city, origin_state = _nested_place(raw, "pickup")
    dest_city, dest_state = _nested_place(raw, "destination")
    dest_city = raw.get("dest_city") or dest_city
    dest_state = raw.get("dest_state") or dest_state
    if not dest_city:
        dest_city, dest_state = _nested_place(raw, "delivery")

    rate = _number(raw.get("rate_usd") or raw.get("rate") or raw.get("price") or raw.get("total_rate"))
    miles = _number(raw.get("loaded_miles") or raw.get("miles") or raw.get("distance"))

    normalized = {
        "id": raw.get("id") or raw.get("load_id") or raw.get("number"),
        "broker": raw.get("broker") or raw.get("broker_name") or raw.get("customer"),
        "origin_city": origin_city,
        "origin_state": origin_state,
        "dest_city": dest_city,
        "dest_state": dest_state,
        "pickup_date": raw.get("pickup_date") or raw.get("pickup_at") or raw.get("pickup"),
        "delivery_date": raw.get("delivery_date") or raw.get("delivery_at"),
        "vehicles": raw.get("vehicles") or raw.get("vehicle_count") or raw.get("units") or 1,
        "rate_usd": rate,
        "loaded_miles": miles,
        "trailer_type": raw.get("trailer_type") or raw.get("trailer") or "open",
        "vehicle_type": raw.get("vehicle_type") or "mixed",
        "notes": raw.get("notes") or raw.get("note") or "",
    }
    return Load.from_dict(normalized)


def _load_file(path: Path) -> LoadBoard:
    suffix = path.suffix.lower()
    try:
        if suffix == ".csv":
            with path.open("r", encoding="utf-8-sig", newline="") as handle:
                records = [dict(row) for row in csv.DictReader(handle)]
        else:
            with path.open("r", encoding="utf-8") as handle:
                records = _extract_records(json.load(handle))
    except ToolError:
        raise
    except (OSError, json.JSONDecodeError, csv.Error) as exc:
        raise ToolError(
            f"could not read loads file '{path.name}': {exc}",
            hint="Fix the JSON/CSV export or unset SUPERDISPATCH_LOADS_FILE to use the demo board.",
        ) from exc

    loads: list[Load] = []
    problems: list[str] = []
    for index, record in enumerate(records, start=1):
        try:
            loads.append(normalize_record(record))
        except ValueError as exc:
            problems.append(f"row {index}: {exc}")
    if not loads:
        detail = "; ".join(problems[:3]) or "the file is empty"
        raise ToolError(
            f"no usable loads in '{path.name}' ({detail})",
            hint="Each load needs id, origin/destination city+state, pickup_date, rate and miles.",
        )
    warning = None
    if problems:
        warning = f"skipped {len(problems)} invalid row(s): " + "; ".join(problems[:3])
        log.warning(warning)
    return LoadBoard(loads=loads, source=f"file:{path.name}", warning=warning)


def _fetch_api() -> LoadBoard:
    url = config.SUPERDISPATCH_API_URL
    token = config.SUPERDISPATCH_API_TOKEN
    scheme = config.SUPERDISPATCH_AUTH_SCHEME
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "Authorization": f"{scheme} {token}".strip(),
            "User-Agent": "LoadPilot/1.0 (student project)",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=config.SUPERDISPATCH_TIMEOUT) as response:
            body = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:300]
        raise ToolError(
            f"SuperDispatch API returned HTTP {exc.code}",
            hint=f"Check the token/endpoint and API subscription. Response: {detail}",
        ) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ToolError(
            f"could not reach the SuperDispatch API: {exc}",
            hint="Check the network/URL, or unset SUPERDISPATCH_API_URL to use the demo board.",
        ) from exc

    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise ToolError(
            "SuperDispatch API returned non-JSON content",
            hint="Verify SUPERDISPATCH_API_URL points to the JSON loads endpoint.",
        ) from exc

    records = _extract_records(payload)
    loads: list[Load] = []
    problems: list[str] = []
    for index, record in enumerate(records, start=1):
        try:
            loads.append(normalize_record(record))
        except ValueError as exc:
            problems.append(f"record {index}: {exc}")
    if not loads:
        raise ToolError(
            "the API response contained no usable loads",
            hint="The account may have no posted loads, or the field mapping needs updating.",
        )
    warning = None
    if problems:
        warning = f"skipped {len(problems)} unusable API record(s): " + "; ".join(problems[:3])
    return LoadBoard(loads=loads, source="superdispatch-api", warning=warning)


def _load_demo() -> LoadBoard:
    with config.DEMO_LOADS_PATH.open("r", encoding="utf-8") as handle:
        records = _extract_records(json.load(handle))
    loads = [normalize_record(record) for record in records]
    return LoadBoard(
        loads=loads,
        source="demo",
        warning=(
            "Demo dataset bundled with the project, not live SuperDispatch data. "
            "Configure SUPERDISPATCH_API_URL/TOKEN or SUPERDISPATCH_LOADS_FILE for real loads."
        ),
    )


_file_cache: dict[str, tuple[float, LoadBoard]] = {}


def load_board() -> LoadBoard:
    """Return the current board using the provider priority described above."""

    if config.SUPERDISPATCH_API_URL and config.SUPERDISPATCH_API_TOKEN:
        try:
            return _fetch_api()
        except ToolError as exc:
            if not config.SUPERDISPATCH_ALLOW_DEMO_FALLBACK:
                raise
            demo = _load_demo()
            demo.warning = f"API failed ({exc.message}); showing demo data instead."
            return demo

    if config.SUPERDISPATCH_API_URL and not config.SUPERDISPATCH_API_TOKEN:
        demo = _load_demo()
        demo.warning = "SUPERDISPATCH_API_URL is set but SUPERDISPATCH_API_TOKEN is missing; using demo data."
        return demo

    path = config.LOADS_FILE
    if path.exists() and path.stat().st_size > 0:
        mtime = path.stat().st_mtime
        cached = _file_cache.get(str(path))
        if cached and cached[0] == mtime:
            return cached[1]
        board = _load_file(path)
        _file_cache[str(path)] = (mtime, board)
        return board

    return _load_demo()


def get_load(load_id: str) -> Load:
    """Find one load by id, with a helpful hint when the id is hallucinated."""

    wanted = str(load_id).strip()
    board = load_board()
    for load in board.loads:
        if load.id.lower() == wanted.lower():
            return load
    ids = [load.id for load in board.loads]
    suggestions = difflib.get_close_matches(wanted, ids, n=3, cutoff=0.5)
    hint = f"Known load IDs include: {', '.join(ids[:8])}."
    if suggestions:
        hint = f"Did you mean {', '.join(suggestions)}? " + hint
    raise ToolError(f"load '{wanted}' was not found on the board", hint=hint)
