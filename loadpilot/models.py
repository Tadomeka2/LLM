"""Load records and the profitability model.

The profit model is deliberately explicit and auditable: the LLM may choose
which numbers to use, but it never does the arithmetic itself.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime
from typing import Any


def _first(raw: dict, *keys: str, default: Any = None) -> Any:
    for key in keys:
        value = raw.get(key)
        if value is not None and value != "":
            return value
    return default


def _as_float(value: Any, field: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"field '{field}' must be a number, got {value!r}") from exc


def _as_int(value: Any, field: str) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"field '{field}' must be an integer, got {value!r}") from exc


def _as_date(value: Any, field: str) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    # API timestamps such as 2026-11-05T08:00:00Z are common.
    candidate = text[:10]
    try:
        date.fromisoformat(candidate)
    except ValueError as exc:
        raise ValueError(f"field '{field}' must be an ISO date (YYYY-MM-DD), got {value!r}") from exc
    return candidate


@dataclass(frozen=True)
class Load:
    """One posted auto-transport load."""

    id: str
    broker: str
    origin_city: str
    origin_state: str
    dest_city: str
    dest_state: str
    pickup_date: str
    delivery_date: str | None
    vehicles: int
    rate_usd: float
    loaded_miles: int
    trailer_type: str
    vehicle_type: str = "mixed"
    notes: str = ""

    @property
    def lane(self) -> str:
        return f"{self.origin_city}, {self.origin_state} -> {self.dest_city}, {self.dest_state}"

    @property
    def gross_rpm(self) -> float:
        return self.rate_usd / self.loaded_miles if self.loaded_miles else 0.0

    @classmethod
    def from_dict(cls, raw: dict) -> "Load":
        if not isinstance(raw, dict):
            raise ValueError("load must be an object")

        load_id = str(_first(raw, "id", "load_id", "number", default="")).strip()
        if not load_id:
            raise ValueError("missing load id")

        required = {
            "origin_city": _first(raw, "origin_city"),
            "origin_state": _first(raw, "origin_state"),
            "dest_city": _first(raw, "dest_city", "destination_city"),
            "dest_state": _first(raw, "dest_state", "destination_state"),
            "pickup_date": _first(raw, "pickup_date", "pickup"),
            "rate_usd": _first(raw, "rate_usd", "rate", "price"),
            "loaded_miles": _first(raw, "loaded_miles", "miles"),
        }
        missing = [name for name, value in required.items() if value in (None, "")]
        if missing:
            raise ValueError(f"missing {', '.join(missing)}")

        return cls(
            id=load_id,
            broker=str(_first(raw, "broker", "broker_name", "customer", default="Unknown broker")).strip(),
            origin_city=str(required["origin_city"]).strip(),
            origin_state=str(required["origin_state"]).strip().upper(),
            dest_city=str(required["dest_city"]).strip(),
            dest_state=str(required["dest_state"]).strip().upper(),
            pickup_date=_as_date(required["pickup_date"], "pickup_date"),
            delivery_date=(
                _as_date(_first(raw, "delivery_date", "delivery"), "delivery_date")
                if _first(raw, "delivery_date", "delivery")
                else None
            ),
            vehicles=_as_int(_first(raw, "vehicles", "vehicle_count", default=1), "vehicles"),
            rate_usd=_as_float(required["rate_usd"], "rate_usd"),
            loaded_miles=_as_int(required["loaded_miles"], "loaded_miles"),
            trailer_type=str(_first(raw, "trailer_type", "trailer", default="open")).strip().lower(),
            vehicle_type=str(_first(raw, "vehicle_type", default="mixed")).strip().lower(),
            notes=str(_first(raw, "notes", "note", default="")).strip(),
        )

    def to_dict(self) -> dict:
        data = asdict(self)
        data["lane"] = self.lane
        data["gross_rpm"] = round(self.gross_rpm, 3)
        return data


def compute_profit_values(
    rate_usd: float,
    loaded_miles: int,
    *,
    load_id: str = "custom",
    lane: str = "custom rate",
    deadhead_miles: float = 0.0,
    mpg: float = 6.5,
    diesel_price: float = 3.75,
    fixed_cost_per_mile: float = 0.35,
    target_effective_rpm: float = 0.45,
) -> dict:
    """Return a full P&L using loaded + deadhead miles.

    Fuel and fixed costs are applied to *total* miles, which is the mistake
    most quick mental calculations make.
    """

    if mpg <= 0:
        raise ValueError("mpg must be greater than 0")
    if diesel_price < 0 or fixed_cost_per_mile < 0 or deadhead_miles < 0:
        raise ValueError("costs and deadhead miles cannot be negative")
    if loaded_miles <= 0:
        raise ValueError("loaded_miles must be greater than 0")

    total_miles = loaded_miles + float(deadhead_miles)
    fuel_cost = total_miles / mpg * diesel_price
    fixed_cost = total_miles * fixed_cost_per_mile
    total_cost = fuel_cost + fixed_cost
    net_profit = rate_usd - total_cost
    effective_rpm = net_profit / total_miles if total_miles else 0.0
    break_even_rate = total_cost

    if effective_rpm >= target_effective_rpm * 1.35:
        verdict = "excellent"
    elif effective_rpm >= target_effective_rpm:
        verdict = "good"
    elif effective_rpm >= target_effective_rpm * 0.7:
        verdict = "marginal"
    else:
        verdict = "pass"

    reasons = {
        "excellent": "net profit per total mile is well above the target",
        "good": "net profit per total mile meets the target",
        "marginal": "thin margin - one delay or extra deadhead erases the profit",
        "pass": "net profit per total mile is below the target",
    }

    return {
        "load_id": load_id,
        "lane": lane,
        "rate_usd": round(rate_usd, 2),
        "loaded_miles": loaded_miles,
        "deadhead_miles": round(float(deadhead_miles), 1),
        "total_miles": round(total_miles, 1),
        "gross_rpm": round(rate_usd / loaded_miles, 3),
        "fuel_cost": round(fuel_cost, 2),
        "fixed_cost": round(fixed_cost, 2),
        "total_cost": round(total_cost, 2),
        "net_profit": round(net_profit, 2),
        "effective_rpm": round(effective_rpm, 3),
        "break_even_rate": round(break_even_rate, 2),
        "verdict": verdict,
        "verdict_reason": reasons[verdict],
        "assumptions": {
            "mpg": mpg,
            "diesel_price": diesel_price,
            "fixed_cost_per_mile": fixed_cost_per_mile,
            "target_effective_rpm": target_effective_rpm,
        },
    }


def compute_profit(load: Load, **kwargs) -> dict:
    """Convenience wrapper for a :class:`Load` record."""

    return compute_profit_values(
        load.rate_usd,
        load.loaded_miles,
        load_id=load.id,
        lane=load.lane,
        **kwargs,
    )
