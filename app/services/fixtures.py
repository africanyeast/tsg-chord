"""Seed loader for /admin/reset. Dates in seed.json are day offsets from the reset day."""

import json
from datetime import date, datetime, timedelta
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.clock import today
from app.models import Conversation, Customer, Event, Measurement, Message, Order, Payment, PendingAction

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"

# Children first, so foreign keys never block the wipe.
TABLES = [Event, PendingAction, Payment, Message, Conversation, Order, Measurement, Customer]


def _day(base: date, offset: int | None) -> date | None:
    return None if offset is None else base + timedelta(days=offset)


def _digits(phone: str | None) -> str:
    return "".join(ch for ch in (phone or "") if ch.isdigit())


def reset(session: Session, fixtures_dir: Path = FIXTURES, on: date | None = None) -> dict[str, int]:
    """Wipe everything (live chats, links, pending actions, logs) and load the seed. Caller commits."""
    base = on or today()
    for table in TABLES:
        session.execute(delete(table))

    seed = json.loads((fixtures_dir / "seed.json").read_text())
    for c in seed["customers"]:
        session.add(
            Customer(
                id=c["id"],
                name=c["name"],
                phone=c.get("phone"),
                email=c.get("email"),
                preferences=c.get("preferences", []),
                notes=c.get("notes"),
            )
        )
        for m in c.get("measurements", []):
            session.add(
                Measurement(
                    customer_id=c["id"],
                    label=m["label"],
                    unit=m.get("unit"),
                    values=m["values"],
                    taken_on=_day(base, m["taken_day_offset"]),
                    recorded_by="seed",
                )
            )
    session.flush()

    explicit = seed.get("payments", [])
    for o in seed["orders"]:
        session.add(
            Order(
                id=o["id"],
                customer_id=o["customer_id"],
                garments=o["garments"],
                price_kobo=o["price_kobo"],
                status=o["status"],
                event_date=_day(base, o.get("event_day_offset")),
                due_date=_day(base, o.get("due_day_offset")),
                fitting_date=_day(base, o.get("fitting_day_offset")),
            )
        )
        # paid_kobo not covered by a listed payment was taken before Paystack (cash or transfer).
        listed = sum(p["amount_kobo"] for p in explicit if p["order_id"] == o["id"] and p["status"] == "confirmed")
        if o["paid_kobo"] > listed:
            session.add(
                Payment(
                    id=f"PAY-{o['id']}-PRIOR",
                    order_id=o["id"],
                    amount_kobo=o["paid_kobo"] - listed,
                    status="confirmed",
                    provider="manual",
                    confirmed_at=datetime.combine(_day(base, o.get("due_day_offset") or 0), datetime.min.time()),
                )
            )
    session.flush()

    for p in explicit:
        confirmed = _day(base, p.get("confirmed_day_offset"))
        session.add(
            Payment(
                id=p["id"],
                order_id=p["order_id"],
                amount_kobo=p["amount_kobo"],
                status=p["status"],
                provider=p.get("provider"),
                reference=p.get("reference"),
                charged_kobo=p.get("charged_kobo"),
                confirmed_at=datetime.combine(confirmed, datetime.min.time()) if confirmed else None,
            )
        )

    for r in seed.get("pending_requests", []):
        event = _day(base, r.get("event_day_offset"))
        session.add(
            PendingAction(
                id=r["id"],
                kind="order_request",
                customer_id=r["customer_id"],
                payload={
                    "garments": r["garments"],
                    "event_date": event.isoformat() if event else None,
                    "event_date_text": None,
                    "notes": r.get("notes"),
                    "source_message_ids": [],
                    "new_customer": None,
                },
                status="pending",
            )
        )
    session.flush()

    by_phone = {_digits(c.phone): c.id for c in session.scalars(select(Customer))}
    conversations = messages = 0
    for path in sorted((fixtures_dir / "conversations").glob("*.json")):
        conv = json.loads(path.read_text())
        msgs = conv["messages"]
        session.add(
            Conversation(
                id=conv["conversation_id"],
                customer_id=by_phone.get(_digits(conv["contact"].get("phone"))),
                contact=conv["contact"],
                source="seed",
                started_at=datetime.fromisoformat(msgs[0]["ts"]),
            )
        )
        session.flush()
        for seq, m in enumerate(msgs, start=1):
            session.add(
                Message(
                    id=f"MSG-{conv['conversation_id']}-{seq:03d}",
                    conversation_id=conv["conversation_id"],
                    seq=seq,
                    ts=datetime.fromisoformat(m["ts"]),
                    sender=m["from"],
                    type=m["type"],
                    text=m.get("text"),
                    media_ref=m.get("media_ref"),
                )
            )
        conversations += 1
        messages += len(msgs)

    session.add(Event(endpoint="admin", tool="reset", outcome="ok", detail={"reset_date": base.isoformat()}))
    session.flush()
    return {
        "customers": len(seed["customers"]),
        "orders": len(seed["orders"]),
        "conversations": conversations,
        "messages": messages,
    }
