"""Customer recognition. A sender ID is linked to a customer only after the server checks an answer."""

import re

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models import Conversation, Customer, Order

VERIFY_QUESTION = "What did you order from us last time? (the garment and its colour)"
PHONE_QUESTION = "What phone number is on your account with us?"

_STOPWORDS = {"and", "with", "the", "a", "of", "set", "piece", "three", "two"}


def _words(text: str | None) -> set[str]:
    return set(re.findall(r"[a-z]+", (text or "").lower()))


def _digits(text: str | None) -> str:
    return re.sub(r"\D", "", text or "")


def by_sender(session: Session, sender_id: str) -> Customer | None:
    return session.scalar(select(Customer).where(Customer.sender_id == sender_id))


def name_matches(session: Session, name: str) -> list[Customer]:
    """Customers whose name contains every word the sender gave, ignoring titles."""
    wanted = _words(name) - {"mr", "mrs", "miss", "ms", "dr", "chief"}
    if not wanted:
        return []
    return [c for c in session.scalars(select(Customer).order_by(Customer.id)) if wanted <= _words(c.name)]


def recent_orders(session: Session, customer_id: str, limit: int = 3) -> list[Order]:
    return session.scalars(
        select(Order)
        .where(Order.customer_id == customer_id)
        .order_by(Order.event_date.desc().nulls_last(), Order.id.desc())
        .limit(limit)
    ).all()


def last_order(session: Session, customer_id: str) -> Order | None:
    orders = recent_orders(session, customer_id, 1)
    return orders[0] if orders else None


def answer_matches(session: Session, customer: Customer, answer: str) -> bool:
    """One of the last three orders: its colour and a garment word must both appear. No orders: the phone number.

    Three, not one: once Simi approves a new order, "what did you order last time" still means the one before.
    """
    orders = recent_orders(session, customer.id)
    if not orders:
        phone = _digits(customer.phone)
        return len(phone) >= 7 and phone[-7:] in _digits(answer)
    said = _words(answer)
    for order in orders:
        for garment in order.garments:
            text = garment.get("type") or garment.get("description")
            type_words = _words(text) - _STOPWORDS - _words(garment.get("colour"))
            colour_words = _words(garment.get("colour"))
            if type_words & said and (not colour_words or colour_words & said):
                return True
    return False


def link(session: Session, customer: Customer, sender_id: str) -> None:
    previous = by_sender(session, sender_id)
    if previous is not None and previous.id != customer.id:
        previous.sender_id = None
    customer.sender_id = sender_id
    session.execute(
        update(Conversation).where(Conversation.sender_id == sender_id).values(customer_id=customer.id)
    )


def identify(session: Session, sender_id: str, name: str, last_order_answer: str | None) -> dict:
    known = by_sender(session, sender_id)
    if known is not None:
        return {"status": "identified", "customer_id": known.id, "name": known.name}

    candidates = name_matches(session, name)
    if not candidates:
        return {
            "status": "new_customer",
            "next_step": "Treat as a new customer. Ask for a phone number to put on the order.",
        }

    if not last_order_answer:
        has_orders = any(last_order(session, c.id) for c in candidates)
        return {
            "status": "needs_verification",
            "question": VERIFY_QUESTION if has_orders else PHONE_QUESTION,
            "next_step": "Ask the question, then call identify_customer again with their answer as last_order_answer.",
        }

    matched = [c for c in candidates if answer_matches(session, c, last_order_answer)]
    if len(matched) == 1:
        link(session, matched[0], sender_id)
        return {"status": "identified", "customer_id": matched[0].id, "name": matched[0].name}
    return {
        "status": "not_verified",
        "next_step": (
            "The answer did not match our records. Do not share any order details. "
            "Ask once more, or offer to continue as a new request for Simi to check."
        ),
    }


def find(session: Session, query: str) -> list[Customer]:
    """Match by phone digits (7+) or by name words."""
    digits = _digits(query)
    if len(digits) >= 7:
        return [c for c in session.scalars(select(Customer)) if digits[-10:] in _digits(c.phone)]
    return name_matches(session, query)
