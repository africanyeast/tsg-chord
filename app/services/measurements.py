"""Dated measurements. Every set is kept; a customer's current value for each part is the latest one recorded."""

import re
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.clock import today
from app.errors import WumiError
from app.models import Measurement, Message, PendingAction
from app.services.customers import by_sender

STALE_DAYS = 180
# A change this large against the last value in the same unit is probably a typo or a misheard voice note.
BIG_CHANGE = 0.15
UNITS = {"in": "in", "inch": "in", "inches": "in", '"': "in", "cm": "cm", "centimetre": "cm", "centimetres": "cm", "centimeter": "cm", "centimeters": "cm"}
LIMITS = {"in": (2, 100), "cm": (5, 255)}


def _part(name: str) -> str:
    return re.sub(r"[^a-z]+", "_", name.strip().lower()).strip("_")


def parse_values(values) -> dict[str, float]:
    """{'Chest': 44} or 'Chest 44, Sleeve 25.5' -> {'chest': 44, 'sleeve': 25.5}."""
    if isinstance(values, str):
        pairs = re.findall(r"([A-Za-z][A-Za-z _-]*?)\s*[:=-]?\s*(\d+(?:\.\d+)?)", values)
        values = {k: v for k, v in pairs}
    if not isinstance(values, dict) or not values:
        raise WumiError("VALIDATION_ERROR", "Give the measurements as body part and number, e.g. chest 44, sleeve 25.")
    out = {}
    for name, value in values.items():
        part = _part(str(name))
        try:
            number = float(str(value).strip().rstrip('"'))
        except ValueError as exc:
            raise WumiError("VALIDATION_ERROR", f"Could not read the number for {name}: '{value}'.") from exc
        if not part:
            raise WumiError("VALIDATION_ERROR", f"Say which body part {value} is for.")
        out[part] = int(number) if number.is_integer() else number
    return out


def _unit(unit: str | None, values: dict[str, float]) -> str:
    if not (unit or "").strip():
        raise WumiError("UNIT_NEEDED", "Ask whether these are in inches or centimetres, then call again with unit.")
    key = unit.strip().lower()
    if key not in UNITS:
        raise WumiError("VALIDATION_ERROR", f"Unit '{unit}' is not inches or centimetres.")
    unit = UNITS[key]
    low, high = LIMITS[unit]
    odd = [p for p, v in values.items() if not low <= v <= high]
    if odd:
        raise WumiError(
            "VALIDATION_ERROR",
            f"{', '.join(f'{p} {values[p]}' for p in odd)} is not a believable measurement in {unit}. Ask the customer to check.",
        )
    return unit


def current(session: Session, customer_id: str) -> dict:
    """Latest value per part, each with its own unit and date, plus the full dated history."""
    sets = session.scalars(
        select(Measurement).where(Measurement.customer_id == customer_id).order_by(Measurement.taken_on, Measurement.id)
    ).all()
    latest: dict[str, dict] = {}
    for m in sets:
        for part, value in m.values.items():
            latest[part] = {"value": value, "unit": m.unit, "taken_on": m.taken_on.isoformat()}
    return {
        "current": latest,
        "history": [
            {"label": m.label, "taken_on": m.taken_on.isoformat(), "unit": m.unit, "values": m.values, "recorded_by": m.recorded_by}
            for m in reversed(sets)
        ],
        "last_taken_on": sets[-1].taken_on.isoformat() if sets else None,
    }


def _changes(previous: dict, values: dict, unit: str) -> list[dict]:
    out = []
    for part, value in values.items():
        old = previous.get(part)
        if old and old["unit"] == unit and old["value"] and abs(value - old["value"]) / old["value"] > BIG_CHANGE:
            out.append({"part": part, "was": old["value"], "now": value, "unit": unit, "was_taken_on": old["taken_on"]})
    return out


def record(
    session: Session,
    customer_id: str,
    values,
    unit: str | None,
    label: str | None,
    taken_on: str | None,
    source_message_ids: list[str] | None,
    recorded_by: str,
) -> dict:
    parsed = parse_values(values)
    unit = _unit(unit, parsed)
    day = today()
    if taken_on:
        try:
            day = date.fromisoformat(taken_on.strip())
        except ValueError as exc:
            raise WumiError("VALIDATION_ERROR", "Give taken_on as YYYY-MM-DD, or leave it out for today.") from exc
    previous = current(session, customer_id)["current"]
    session.add(
        Measurement(
            customer_id=customer_id,
            label=(label or "").strip() or "Measurements",
            unit=unit,
            values=parsed,
            taken_on=day,
            recorded_by=recorded_by,
            source_message_ids=_known_messages(session, source_message_ids),
        )
    )
    session.flush()
    changes = _changes(previous, parsed, unit)
    return {
        "status": "recorded",
        "recorded": {"taken_on": day.isoformat(), "unit": unit, "values": parsed},
        "big_changes": changes,
        "next_step": (
            "Read the numbers back. Some changed a lot since last time; ask whether that is right." if changes
            else "Read the numbers back in one line so the customer can correct any."
        ),
        **current(session, customer_id),
    }


def _known_messages(session: Session, ids: list[str] | None) -> list[str]:
    if not ids:
        return []
    known = set(session.scalars(select(Message.id).where(Message.id.in_(ids))))
    return [m for m in ids if m in known]


def record_for_sender(session: Session, sender_id: str, values, unit, label, source_message_ids) -> dict:
    """A verified customer's measurements go on their record; a new customer's wait on their open request."""
    customer = by_sender(session, sender_id)
    if customer is not None:
        return record(session, customer.id, values, unit, label, None, source_message_ids, "customer")
    request = session.scalar(
        select(PendingAction)
        .where(PendingAction.sender_id == sender_id, PendingAction.kind == "order_request", PendingAction.status == "pending")
        .order_by(PendingAction.created_at.desc())
    )
    if request is None:
        raise WumiError(
            "NOT_IDENTIFIED",
            "Verify the customer with identify_customer first. A new customer needs an order request before measurements.",
        )
    parsed = parse_values(values)
    unit = _unit(unit, parsed)
    entry = {"label": (label or "").strip() or "Measurements", "unit": unit, "values": parsed, "taken_on": today().isoformat(),
             "source_message_ids": _known_messages(session, source_message_ids)}
    # Reassign so the JSON column is marked dirty.
    request.payload = {**request.payload, "measurements": [*request.payload.get("measurements", []), entry]}
    return {
        "status": "recorded_with_request",
        "request_id": request.id,
        "recorded": {"taken_on": entry["taken_on"], "unit": unit, "values": parsed},
        "next_step": "Read the numbers back. They are saved with the request and move to the customer's record when Simi approves.",
    }


def apply_from_request(session: Session, customer_id: str, payload: dict) -> None:
    for m in payload.get("measurements", []):
        session.add(
            Measurement(
                customer_id=customer_id,
                label=m["label"],
                unit=m["unit"],
                values=m["values"],
                taken_on=date.fromisoformat(m["taken_on"]),
                recorded_by="customer",
                source_message_ids=m.get("source_message_ids") or [],
            )
        )


def mine(session: Session, sender_id: str) -> dict:
    customer = by_sender(session, sender_id)
    if customer is None:
        raise WumiError("NOT_IDENTIFIED", "Verify the customer with identify_customer before sharing measurements.")
    data = current(session, customer.id)
    if not data["history"]:
        return {"result": "no results", "message": "No measurements on record yet."}
    stale = (today() - date.fromisoformat(data["last_taken_on"])).days > STALE_DAYS
    return {**data, "stale": stale, "note": "Older than 6 months; suggest re-measuring at the fitting." if stale else None}


def needs_measuring(session: Session, customer_id: str) -> str | None:
    """Why an open order's customer should be measured, or None."""
    sets = session.scalars(select(Measurement.taken_on).where(Measurement.customer_id == customer_id)).all()
    if not sets:
        return "no measurements on record"
    age = (today() - max(sets)).days
    if age > STALE_DAYS:
        return f"last measured {age} days ago"
    return None
