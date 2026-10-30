"""Pending actions: created by the front desk, consumed only by the owner endpoint."""

from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.clock import now
from app.errors import WumiError
from app.models import Customer, Message, Order, PendingAction
from app.services import measurements
from app.services.customers import _digits, _words, by_sender, link
from app.services.payments import create_link, naira, parse_naira


def normalise_garments(garments) -> list[dict]:
    if isinstance(garments, str):
        garments = [garments]
    out = []
    for g in garments or []:
        if isinstance(g, str) and g.strip():
            out.append({"description": g.strip()})
        elif isinstance(g, dict) and any(str(v).strip() for v in g.values()):
            out.append({k: v for k, v in g.items() if v not in (None, "")})
    return out


def _next_id(session: Session) -> str:
    count = session.scalar(select(func.count()).select_from(PendingAction))
    return f"REQ-{count + 1:04d}"


def request_order(
    session: Session,
    sender_id: str,
    garments,
    event_date: str | None,
    notes: str | None,
    source_message_ids: list[str] | None,
    name: str | None,
    phone: str | None,
) -> dict:
    items = normalise_garments(garments)
    if not items:
        raise WumiError("VALIDATION_ERROR", "Say what garment(s) the customer wants before sending the request.")

    customer = by_sender(session, sender_id)
    new_customer = None
    if customer is None:
        if not (name and len(_digits(phone)) >= 7):
            raise WumiError(
                "NOT_IDENTIFIED",
                "This sender is not identified. Verify them with identify_customer, "
                "or, for a new customer, pass their name and phone number.",
            )
        new_customer = {"name": name.strip(), "phone": phone.strip()}

    parsed_date, date_text = None, None
    if event_date:
        try:
            parsed_date = date.fromisoformat(event_date.strip()).isoformat()
        except ValueError:
            date_text = event_date.strip()

    sources = []
    if source_message_ids:
        known = set(session.scalars(select(Message.id).where(Message.id.in_(source_message_ids))))
        sources = [m for m in source_message_ids if m in known]

    payload = {
        "garments": items,
        "event_date": parsed_date,
        "event_date_text": date_text,
        "notes": (notes or "").strip() or None,
        "source_message_ids": sources,
        "new_customer": new_customer,
    }

    # A retried call for the same garments returns the open request instead of filing a second one.
    for open_req in session.scalars(
        select(PendingAction).where(
            PendingAction.sender_id == sender_id,
            PendingAction.kind == "order_request",
            PendingAction.status == "pending",
        )
    ):
        if open_req.payload["garments"] == items:
            return {"status": "already_requested", "request_id": open_req.id, "next_step": _NEXT_STEP}

    req = PendingAction(
        id=_next_id(session),
        kind="order_request",
        customer_id=customer.id if customer else None,
        sender_id=sender_id,
        payload=payload,
        status="pending",
    )
    session.add(req)
    session.flush()
    return {"status": "requested", "request_id": req.id, "next_step": _NEXT_STEP}


_NEXT_STEP = (
    "Tell the customer Simi will confirm the price and deposit, and they can ask for an update any time. "
    "Do not quote a price or promise a date."
)


def _customer_label(session: Session, action: PendingAction) -> dict:
    if action.customer_id:
        c = session.get(Customer, action.customer_id)
        return {"customer_id": c.id, "name": c.name, "phone": c.phone, "new_customer": False}
    nc = action.payload.get("new_customer") or {}
    return {"customer_id": None, "name": nc.get("name"), "phone": nc.get("phone"), "new_customer": True}


def list_pending(session: Session) -> dict:
    actions = session.scalars(
        select(PendingAction).where(PendingAction.status == "pending").order_by(PendingAction.created_at)
    ).all()
    if not actions:
        return {"result": "no results", "message": "Nothing is waiting for approval."}
    out = []
    for a in actions:
        ids = a.payload.get("source_message_ids") or []
        sources = session.scalars(select(Message).where(Message.id.in_(ids)).order_by(Message.id)).all() if ids else []
        out.append(
            {
                "request_id": a.id,
                "customer": _customer_label(session, a),
                "garments": a.payload["garments"],
                "event_date": a.payload.get("event_date") or a.payload.get("event_date_text"),
                "notes": a.payload.get("notes"),
                "requested_at": a.created_at.isoformat() if a.created_at else None,
                "source_messages": [{"id": m.id, "text": m.text} for m in sources],
            }
        )
    return {"pending": out, "count": len(out)}


def _resolve(session: Session, request_id: str | None, customer: str | None) -> PendingAction:
    """Find the request the owner means: by ID, else by customer name, else the only one waiting."""
    if request_id:
        action = session.scalar(
            select(PendingAction).where(PendingAction.id == request_id.strip().upper()).with_for_update()
        )
        if action is None:
            raise WumiError("NOT_FOUND", f"No request {request_id}. Call list_pending to see what is waiting.")
        return action
    pending = session.scalars(
        select(PendingAction).where(PendingAction.status == "pending").with_for_update()
    ).all()
    if customer:
        wanted = _words(customer) - {"mr", "mrs", "miss", "ms"}
        pending = [a for a in pending if wanted and wanted <= _words(_customer_label(session, a)["name"])]
    if not pending:
        raise WumiError("NOTHING_PENDING", "Nothing matching is waiting for approval.")
    if len(pending) > 1:
        raise WumiError(
            "AMBIGUOUS_MATCH",
            "More than one request matches: " + ", ".join(a.id for a in pending) + ". Say which request ID.",
        )
    return pending[0]


def _next_number(session: Session, model, prefix: str, width: int) -> str:
    ids = session.scalars(select(model.id).where(model.id.like(f"{prefix}%"))).all()
    nums = [int(i[len(prefix):]) for i in ids if i[len(prefix):].isdigit()]
    return f"{prefix}{(max(nums) + 1 if nums else 1):0{width}d}"


def approve(
    session: Session,
    request_id: str | None,
    customer: str | None,
    price,
    deposit,
    due_date: str | None,
) -> dict:
    action = _resolve(session, request_id, customer)
    if action.status != "pending":
        # Repeats return the first result and change nothing.
        return {"status": "already_done", "request_status": action.status, **(action.result or {})}

    price_kobo = parse_naira(price, "price")
    deposit_kobo = parse_naira(deposit, "deposit")
    if not price_kobo or price_kobo <= 0:
        raise WumiError("VALIDATION_ERROR", "Give the agreed price in naira.")
    if deposit_kobo is None or deposit_kobo <= 0 or deposit_kobo > price_kobo:
        raise WumiError("VALIDATION_ERROR", "Give a deposit above zero and no more than the price.")
    due = None
    if due_date:
        try:
            due = date.fromisoformat(due_date.strip())
        except ValueError as exc:
            raise WumiError("VALIDATION_ERROR", "Give the due date as YYYY-MM-DD.") from exc

    payload = action.payload
    if action.customer_id:
        cust = session.get(Customer, action.customer_id)
    else:
        nc = payload["new_customer"]
        cust = Customer(id=_next_number(session, Customer, "CUS-", 3), name=nc["name"], phone=nc["phone"])
        session.add(cust)
        session.flush()
        link(session, cust, action.sender_id)

    event = date.fromisoformat(payload["event_date"]) if payload.get("event_date") else None
    notes = "; ".join(x for x in [payload.get("notes"), payload.get("event_date_text")] if x) or None
    order = Order(
        id=_next_number(session, Order, "ORD-", 4),
        customer_id=cust.id,
        garments=payload["garments"],
        price_kobo=price_kobo,
        deposit_kobo=deposit_kobo,
        status="confirmed",
        event_date=event,
        due_date=due,
        notes=notes,
    )
    session.add(order)
    session.flush()
    payment = create_link(session, order, deposit_kobo, "deposit")
    measurements.apply_from_request(session, cust.id, payload)

    action.status = "approved"
    action.customer_id = cust.id
    action.resolved_at = now()
    action.result = {
        "request_id": action.id,
        "order_id": order.id,
        "customer": cust.name,
        "price": naira(price_kobo),
        "deposit": naira(deposit_kobo),
        "due_date": due.isoformat() if due else None,
        "payment_link": payment.authorization_url,
        "reference": payment.reference,
    }
    return {
        "status": "approved",
        **action.result,
        "next_step": "The customer sees the price, deposit and link the next time they message Wumi.",
    }


def reject(session: Session, request_id: str | None, customer: str | None, reason: str) -> dict:
    if not (reason or "").strip():
        raise WumiError("VALIDATION_ERROR", "Give a reason; the customer will see it.")
    action = _resolve(session, request_id, customer)
    if action.status != "pending":
        return {"status": "already_done", "request_status": action.status, **(action.result or {})}
    action.status = "rejected"
    action.resolved_at = now()
    action.result = {"request_id": action.id, "reason": reason.strip()}
    return {"status": "rejected", **action.result}
