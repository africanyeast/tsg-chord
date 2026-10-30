"""Payment service functions. A balance is always price minus confirmed payments."""

import logging
import re
import secrets
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.clock import now, today
from app.config import get_settings
from app.db import SessionLocal
from app.errors import WumiError
from app.integrations import paystack
from app.models import Event, Order, Payment

logger = logging.getLogger("wumi.paystack")


def paid_kobo(session: Session, order_id: str) -> int:
    total = session.scalar(
        select(func.coalesce(func.sum(Payment.amount_kobo), 0)).where(
            Payment.order_id == order_id, Payment.status == "confirmed"
        )
    )
    return int(total)


def balance_kobo(session: Session, order: Order) -> int | None:
    if order.price_kobo is None:
        return None
    return order.price_kobo - paid_kobo(session, order.id)


def days_overdue(order: Order, on: date | None = None) -> int:
    """Days past the due date. Callers only count it when a balance is owed."""
    if order.due_date is None:
        return 0
    return max(0, ((on or today()) - order.due_date).days)


def payment_state(session: Session, order: Order, on: date | None = None) -> str:
    """paid | unconfirmed_deposit | overdue | production | awaiting_price."""
    balance = balance_kobo(session, order)
    if balance is None:
        return "awaiting_price"
    if balance <= 0:
        return "paid"
    has_claim = session.scalar(
        select(func.count()).where(Payment.order_id == order.id, Payment.status == "claimed_unconfirmed")
    )
    if has_claim and paid_kobo(session, order.id) == 0:
        return "unconfirmed_deposit"
    if days_overdue(order, on) > 0:
        return "overdue"
    return "production"


def naira(kobo: int | None) -> str | None:
    if kobo is None:
        return None
    whole, rest = divmod(kobo, 100)
    return f"₦{whole:,}" + (f".{rest:02d}" if rest else "")


def parse_naira(value: str | int | float | None, field: str) -> int | None:
    """'85k', '₦85,000', '85000' or 85000 (naira) -> kobo."""
    if value is None or value == "":
        return None
    text = str(value).strip().lower().replace("₦", "").replace("naira", "").replace("ngn", "").replace(",", "").strip()
    match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(k|m)?", text)
    if not match:
        raise WumiError("VALIDATION_ERROR", f"Could not read {field} '{value}'. Give an amount in naira, e.g. 85000 or 85k.")
    amount = float(match.group(1)) * {"k": 1_000, "m": 1_000_000, None: 1}[match.group(2)]
    return round(amount * 100)


def create_link(session: Session, order: Order, amount_kobo: int, purpose: str) -> Payment:
    """Initialise a Paystack checkout for part of an order and record it as a pending payment."""
    settings = get_settings()
    reference = f"WUMI-{order.id}-{secrets.token_hex(4).upper()}"
    # Simulator customers have no email; Paystack needs one on a real domain.
    email = f"{order.customer_id.lower()}@{settings.public_base_url.host}"
    url = paystack.initialize(
        email=email,
        amount_kobo=amount_kobo,
        reference=reference,
        callback_url=f"{str(settings.public_base_url).rstrip('/')}/paid",
        metadata={"order_id": order.id, "purpose": purpose},
    )
    payment = Payment(
        id=f"PAY-{reference}",
        order_id=order.id,
        amount_kobo=amount_kobo,
        status="pending",
        provider="paystack",
        reference=reference,
        authorization_url=url,
    )
    session.add(payment)
    session.flush()
    return payment


def apply_paystack_result(session: Session, reference: str, amount_kobo: int | None, status: str | None) -> dict:
    """The only way money reaches the ledger. Used by check_payment, the webhook and the /paid callback."""
    payment = session.scalar(select(Payment).where(Payment.reference == reference).with_for_update())
    if payment is None:
        raise WumiError("NOT_FOUND", f"No payment with reference {reference}.")
    if payment.status in ("confirmed", "held"):
        return {"result": "already_" + payment.status, "payment": payment}
    if status == "success":
        payment.charged_kobo = amount_kobo
        order = session.get(Order, payment.order_id)
        # Two links paid for one balance would overpay; hold it for Simi rather than go below zero.
        overpaid = balance_kobo(session, order) - (amount_kobo or 0) < 0
        if amount_kobo == payment.amount_kobo and not overpaid:
            payment.status = "confirmed"
            payment.confirmed_at = now()
            return {"result": "confirmed", "payment": payment}
        # The fee bearer or a tampered checkout can change the amount; never apply it automatically.
        payment.status = "held"
        return {"result": "held", "payment": payment}
    if status == "failed":
        payment.status = "failed"
        return {"result": "failed", "payment": payment}
    return {"result": status or "pending", "payment": payment}


def apply_and_log(reference: str, verified: dict, source: str) -> str:
    """Apply a Paystack result in its own transaction and log it. Shared by the webhook, /paid and the reconciler,
    so whichever path confirms first wins and the others get already_confirmed."""
    with SessionLocal() as session:
        try:
            outcome = apply_paystack_result(session, reference, verified.get("amount"), verified.get("status"))["result"]
        except WumiError as exc:
            session.rollback()
            outcome = exc.code
        detail = {"reference": reference, "requested": verified.get("amount"), "charged": verified.get("charged"), "fees": verified.get("fees")}
        session.add(Event(endpoint="webhook", tool=source, outcome=outcome, detail=detail))
        session.commit()
    logger.info(
        "paystack %s %s requested=%s charged=%s fees=%s -> %s",
        source, reference, detail["requested"], detail["charged"], detail["fees"], outcome,
    )
    return outcome
