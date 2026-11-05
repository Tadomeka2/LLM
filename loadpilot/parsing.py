"""Tolerant parser for the model's one-JSON-object protocol.

Small local models often wrap JSON in markdown fences or add a sentence
before it. The parser recovers the last valid object and turns malformed
output into a correction message instead of crashing the agent.
"""

from __future__ import annotations

import json
import re

_SPECIAL_TOKENS = ("<|im_end|>", "<|endoftext|>", "<|end|>")


def _clean(text: str) -> str:
    value = text.strip()
    for token in _SPECIAL_TOKENS:
        value = value.replace(token, "")
    # Unwrap a single fenced block if present.
    fence = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", value, flags=re.DOTALL)
    if fence:
        value = fence.group(1).strip()
    return value


def _iter_json_objects(text: str):
    depth = 0
    start: int | None = None
    in_string = False
    escaped = False
    for index, char in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            if depth == 0:
                start = index
            depth += 1
        elif char == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start is not None:
                    yield text[start : index + 1]
                    start = None


def _normalise(obj: dict) -> tuple[dict | None, str | None]:
    has_tool = "tool" in obj or ("name" in obj and ("args" in obj or "arguments" in obj))
    has_answer = "answer" in obj
    if has_tool and has_answer:
        return None, "the JSON object contains both 'tool' and 'answer'; choose exactly one"

    if has_tool:
        name = obj.get("tool", obj.get("name"))
        args = obj.get("args", obj.get("arguments", {}))
        if not isinstance(name, str) or not name.strip():
            return None, "'tool' must be a non-empty string"
        if args is None:
            args = {}
        if not isinstance(args, dict):
            return None, "'args' must be a JSON object"
        return {"tool": name.strip(), "args": args}, None

    if has_answer:
        answer = obj["answer"]
        if not isinstance(answer, str):
            answer = str(answer)
        if not answer.strip():
            return None, "'answer' must not be empty"
        return {"answer": answer.strip()}, None

    return None, "the JSON object must contain either 'tool' or 'answer'"


def extract_action(text: str) -> tuple[dict | None, str | None]:
    """Return ``(action, error)`` where action is a tool call or final answer."""

    cleaned = _clean(text)
    if not cleaned:
        return None, "the response was empty"

    # Fast path: the whole response is one object.
    try:
        whole = json.loads(cleaned)
    except json.JSONDecodeError:
        whole = None
    if isinstance(whole, dict):
        action, error = _normalise(whole)
        if action:
            return action, None
        # Keep scanning: the model may have printed an example first.
        if "tool" not in whole and "answer" not in whole:
            pass
        else:
            return None, error

    candidates: list[dict] = []
    for chunk in _iter_json_objects(cleaned):
        try:
            value = json.loads(chunk)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            candidates.append(value)

    for candidate in reversed(candidates):
        if not any(key in candidate for key in ("tool", "answer", "name")):
            continue
        action, error = _normalise(candidate)
        if action:
            return action, None
        return None, error

    return None, (
        "no JSON object with 'tool' or 'answer' was found. "
        'Reply with exactly {"tool":"<name>","args":{...}} or {"answer":"..."}.'
    )
