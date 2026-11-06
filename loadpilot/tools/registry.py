"""Tool registry, JSON-schema validation and safe execution."""

from __future__ import annotations

import difflib
import inspect
import logging
from dataclasses import dataclass
from typing import Any, Callable

from ..errors import ToolError

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    parameters: dict
    func: Callable[..., dict]

    def schema(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
        }


_REGISTRY: dict[str, Tool] = {}


def tool(name: str, description: str, parameters: dict):
    """Decorator that registers a function as an agent tool."""

    def register(func: Callable[..., dict]) -> Callable[..., dict]:
        if name in _REGISTRY:
            raise RuntimeError(f"tool '{name}' is already registered")
        _REGISTRY[name] = Tool(name=name, description=description, parameters=parameters, func=func)
        return func

    return register


def get_tool(name: str) -> Tool | None:
    return _REGISTRY.get(name)


def describe_tools() -> list[dict]:
    return [tool_.schema() for tool_ in _REGISTRY.values()]


def _coerce(value: Any, spec: dict, name: str) -> Any:
    kind = spec.get("type")
    if kind == "string":
        return str(value)
    if kind in {"number", "integer"}:
        if isinstance(value, bool):
            raise ToolError(f"parameter '{name}' must be a {kind}, not a boolean")
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            raise ToolError(f"parameter '{name}' must be a {kind}, got {value!r}") from exc
        if kind == "integer":
            if not number.is_integer():
                raise ToolError(f"parameter '{name}' must be a whole number, got {value!r}")
            return int(number)
        return number
    if kind == "boolean":
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.lower() in {"true", "false"}:
            return value.lower() == "true"
        raise ToolError(f"parameter '{name}' must be true or false, got {value!r}")
    if kind == "array":
        if not isinstance(value, list):
            raise ToolError(f"parameter '{name}' must be a list")
        return value
    return value


def _validate(tool_: Tool, args: dict) -> dict:
    if not isinstance(args, dict):
        raise ToolError(
            "tool arguments must be a JSON object",
            hint='Example: {"tool":"search_loads","args":{"origin_state":"TX"}}',
        )
    properties = tool_.parameters.get("properties", {})
    required = tool_.parameters.get("required", [])

    unknown = sorted(set(args) - set(properties))
    if unknown:
        raise ToolError(
            f"unknown parameter(s) for {tool_.name}: {', '.join(unknown)}",
            hint=f"Allowed parameters: {', '.join(properties)}.",
        )

    cleaned: dict[str, Any] = {}
    for key, value in args.items():
        if value is None:
            continue
        spec = properties[key]
        cleaned[key] = _coerce(value, spec, key)
        allowed = spec.get("enum")
        if allowed and cleaned[key] not in allowed:
            raise ToolError(
                f"parameter '{key}' must be one of {allowed}, got {cleaned[key]!r}",
            )

    missing = [key for key in required if cleaned.get(key) in (None, "")]
    if missing:
        raise ToolError(
            f"missing required parameter(s) for {tool_.name}: {', '.join(missing)}",
            hint="Parameters: " + ", ".join(
                f"{key} ({properties[key].get('type', 'any')})" for key in properties
            ),
        )
    return cleaned


def call_tool(name: str, args: dict) -> dict:
    """Execute a tool and always return a JSON-serializable observation."""

    requested_name = name
    tool_ = get_tool(name)
    if tool_ is None:
        # Small models sometimes emit a close variant ("watch_list",
        # "market-stats", "calculate_profits"). Resolve it instead of losing
        # the whole turn; the observation records the substitution.
        close = difflib.get_close_matches(name, list(_REGISTRY), n=1, cutoff=0.6)
        if not close:
            return ToolError(
                f"unknown tool '{name}'",
                hint="Available tools: " + ", ".join(_REGISTRY) + ".",
            ).to_dict()
        tool_ = get_tool(close[0])
        name = tool_.name

    try:
        cleaned = _validate(tool_, args if args is not None else {})
        result = tool_.func(**cleaned)
    except ToolError as exc:
        return exc.to_dict()
    except TypeError as exc:
        signature = inspect.signature(tool_.func)
        return {
            "ok": False,
            "error": f"could not call {name}: {exc}",
            "hint": f"Expected parameters: {', '.join(signature.parameters)}",
        }
    except Exception as exc:  # noqa: BLE001 - tools must never crash the loop
        log.exception("unexpected error in tool %s", name)
        return {
            "ok": False,
            "error": f"internal error in {name}: {type(exc).__name__}: {exc}",
            "hint": "This is a bug, not a bad request. Try another tool or explain the failure.",
        }

    if not isinstance(result, dict):
        result = {"ok": True, "result": result}
    result.setdefault("ok", True)
    if requested_name != name:
        result["resolved_tool"] = {"requested": requested_name, "used": name}
    return result
