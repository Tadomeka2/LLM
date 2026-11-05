"""Domain errors that are safe to show to the language model."""

from __future__ import annotations


class ToolError(Exception):
    """An expected, user-fixable tool failure.

    ``hint`` is appended to the observation so the model can correct itself
    (for example by using a valid US state code) instead of guessing.
    """

    def __init__(self, message: str, hint: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint

    def to_dict(self) -> dict:
        payload = {"ok": False, "error": self.message}
        if self.hint:
            payload["hint"] = self.hint
        return payload
