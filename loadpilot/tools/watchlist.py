"""Persistent watchlist tool."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone

from .. import config
from ..data_source import get_load
from ..errors import ToolError
from ..filters import load_summary
from .registry import tool


def _read() -> list[dict]:
    path = config.WATCHLIST_FILE
    if not path.exists() or path.stat().st_size == 0:
        return []
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise ToolError(
            f"watchlist file '{path.name}' is unreadable: {exc}",
            hint=f"Fix or delete {path}, then try again.",
        ) from exc
    if not isinstance(data, list):
        raise ToolError(
            f"watchlist file '{path.name}' must contain a JSON array",
            hint=f"Delete {path} to start a clean watchlist.",
        )
    return [entry for entry in data if isinstance(entry, dict)]


def _write(entries: list[dict]) -> None:
    path = config.WATCHLIST_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(entries, handle, ensure_ascii=False, indent=2)
    os.replace(temporary, path)


@tool(
    name="watchlist",
    description=(
        "Manage the driver's saved-load watchlist: add, list, remove or clear "
        "entries. Adding validates that the load exists on the board and stores "
        "a snapshot of the rate and lane."
    ),
    parameters={
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["add", "list", "remove", "clear"],
                "description": "Watchlist operation.",
            },
            "load_id": {"type": "string", "description": "Load ID (required for add/remove)."},
            "note": {"type": "string", "description": "Optional note, e.g. 'call broker tomorrow'."},
            "confirm": {"type": "boolean", "description": "Must be true to clear the whole list."},
        },
        "required": ["action"],
    },
)
def watchlist(
    action: str,
    load_id: str | None = None,
    note: str | None = None,
    confirm: bool = False,
) -> dict:
    """Add, list, remove or clear saved loads.

    Name:
        watchlist

    Description:
        A small persistent JSON store (``data/watchlist.json`` by default).
        ``add`` validates the load against the current board and stores a
        snapshot so the entry still makes sense if the board changes later.

    Parameters:
        action (str): "add", "list", "remove" or "clear".
        load_id (str, optional): required for add and remove.
        note (str, optional): free-text note for add.
        confirm (bool): must be true to clear the entire list.

    Returns:
        dict: ``{ok, action, count, entries, entry?, removed?, hint?}``.
        Unknown load IDs return ``{ok: false, error, hint}`` with close matches.
    """

    entries = _read()

    if action == "list":
        ordered = sorted(entries, key=lambda item: item.get("added_at", ""), reverse=True)
        return {"ok": True, "action": "list", "count": len(ordered), "entries": ordered}

    if action == "add":
        if not load_id:
            raise ToolError("load_id is required for action='add'")
        load = get_load(load_id)
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        existing = next(
            (item for item in entries if str(item.get("load_id", "")).lower() == load.id.lower()),
            None,
        )
        if existing:
            if note is not None:
                existing["note"] = str(note)[:300]
            existing["updated_at"] = now
            _write(entries)
            return {
                "ok": True,
                "action": "add",
                "updated": True,
                "count": len(entries),
                "entry": existing,
                "hint": "Load was already on the watchlist; its note was updated.",
            }

        entry = {
            "load_id": load.id,
            "note": str(note or "")[:300],
            "added_at": now,
            "snapshot": load_summary(load),
        }
        entries.append(entry)
        _write(entries)
        return {"ok": True, "action": "add", "updated": False, "count": len(entries), "entry": entry}

    if action == "remove":
        if not load_id:
            raise ToolError("load_id is required for action='remove'")
        wanted = str(load_id).strip().lower()
        kept = [item for item in entries if str(item.get("load_id", "")).lower() != wanted]
        removed = len(entries) - len(kept)
        _write(kept)
        payload = {"ok": True, "action": "remove", "removed": bool(removed), "count": len(kept)}
        if not removed:
            payload["hint"] = f"'{load_id}' was not on the watchlist."
        return payload

    if action == "clear":
        if not confirm:
            raise ToolError(
                "refusing to clear the watchlist without confirmation",
                hint="Call again with confirm=true if you really want to delete every entry.",
            )
        _write([])
        return {"ok": True, "action": "clear", "removed": len(entries), "count": 0}

    raise ToolError(f"unknown action '{action}'", hint="Use add, list, remove or clear.")
