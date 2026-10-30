"""Order views for the front desk."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import WumiError
from app.models import Order, Payment, PendingAction
from app.services.customers import by_sender
from app.integrations import paystack
from app.services.payments import apply_paystack_result, balance_kobo, naira, paid_kobo


def _order_view(session: Session, order: Order) -> dict:
    balance = balance_kobo(session, order)
    link = session.scalar(
        select(Payment)
        .where(Payment.order_id == order.id, Payment.status == "pending", Payment.authorization_url.is_not(None))
        .order_by(Payment.created_at.desc())
        .limit(1)
    )
    return {
        "order_id": order.id,
        "garments": order.garments,
        "status": order.status,
        "price": naira(order.price_kobo),
        "deposit": naira(order.deposit_kobo),
        "paid": naira(paid_kobo(session, order.id)),
        "balance": naira(balance),
        "event_date": order.event_date.isoformat() if order.event_date else None,
        "due_date": order.due_date.isoformat() if order.due_date else None,
        "payment_link": {"amount": naira(link.amount_kobo), "url": link.authorization_url} if link else None,
    }


def my_orders(session: Session, sender_id: str) -> dict:
    customer = by_sender(session, sender_id)
    requests = session.scalars(
        select(PendingAction)
        .where(PendingAction.sender_id == sender_id, PendingAction.status.in_(["pending", "rejected"]))
        .order_by(PendingAction.created_at)
    ).all()
    if customer is None and not requests:
        raise WumiError("NOT_IDENTIFIED", "Verify the customer with identify_customer before sharing any order details.")

    orders = []
    if customer is not None:
        orders = session.scalars(
            select(Order).where(Order.customer_id == customer.id).order_by(Order.created_at.desc(), Order.id.desc())
        ).all()
    req_views = [
        {
            "request_id": r.id,
            "garments": r.payload["garments"],
            "status": "waiting for Simi" if r.status == "pending" else "declined by Simi",
            "reason": (r.result or {}).get("reason") if r.status == "rejected" else None,
        }
        for r in requests
    ]
    if not orders and not req_views:
        return {"result": "no results", "message": "No orders or requests on record for this customer."}
    return {"orders": [_order_view(session, o) for o in orders], "requests": req_views}


_SAY = {
    "confirmed": "Payment received and confirmed by Paystack.",
    "already_confirmed": "Payment received and confirmed by Paystack.",
    "held": "Paystack received a different amount from what was asked. Simi will check it and get back to you.",
    "already_held": "Paystack received a different amount from what was asked. Simi will check it and get back to you.",
    "failed": "Paystack says this payment failed. Nothing was taken. You can try the same link again or ask for a new one.",
}


def check_payment(session: Session, sender_id: str) -> dict:
    """Ask Paystack about the sender's recent payment links. A customer's word or a screenshot changes nothing."""
    customer = by_sender(session, sender_id)
    if customer is None:
        raise WumiError("NOT_IDENTIFIED", "Verify the customer with identify_customer first.")
    links = session.scalars(
        select(Payment)
        .join(Order, Order.id == Payment.order_id)
        .where(Order.customer_id == customer.id, Payment.provider == "paystack", Payment.reference.is_not(None))
        .order_by(Payment.created_at.desc())
        .limit(5)
    ).all()
    if not links:
        return {
            "status": "no_payment_link",
            "message": "There is no Paystack payment on record for this customer, so nothing has changed.",
            "next_step": "Payments are confirmed only through a Paystack link from Simi. Offer to pass a message to Simi.",
        }
    # The customer may have paid an older link, so ask Paystack about every open one, newest first.
    checked = []
    for link in links:
        if link.status in ("confirmed", "held"):
            checked.append((link, "already_" + link.status))
            continue
        verified = paystack.verify(link.reference)
        checked.append((link, apply_paystack_result(session, link.reference, verified["amount"], verified["status"])["result"]))
    fresh = [c for c in checked if c[1] in ("confirmed", "held")]
    payment, result = (fresh or checked)[0]
    order = session.get(Order, payment.order_id)
    return {
        "status": result,
        "message": _SAY.get(result, "Paystack has not received this payment yet, so nothing has changed. Pay with the link from Simi; if you just paid, wait a minute and ask again."),
        "order_id": order.id,
        "amount": naira(payment.amount_kobo),
        "balance_now": naira(balance_kobo(session, order)),
    }
