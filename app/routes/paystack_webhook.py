"""Paystack webhook and checkout callback. Both confirm money only through apply_and_log."""

import json

from sqlalchemy import select
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, Response

from app.db import SessionLocal
from app.errors import WumiError
from app.integrations import paystack
from app.models import Customer, Event, Order, Payment
from app.services import briefs
from app.services.payments import apply_and_log, naira
from app.templating import templates


async def webhook(request: Request) -> Response:
    body = await request.body()
    if not paystack.valid_signature(body, request.headers.get("x-paystack-signature")):
        with SessionLocal() as session:
            session.add(Event(endpoint="webhook", tool="paystack", outcome="BAD_SIGNATURE"))
            session.commit()
        return PlainTextResponse("Invalid signature", status_code=401)
    try:
        event = json.loads(body)
    except ValueError:
        return PlainTextResponse("Bad body", status_code=400)
    data = event.get("data") or {}
    if event.get("event") == "charge.success" and data.get("reference"):
        outcome = apply_and_log(data["reference"], {**paystack.summary(data), "status": "success"}, "paystack")
        return JSONResponse({"received": True, "outcome": outcome})
    return JSONResponse({"received": True, "outcome": "ignored"})


MAX_CHECKS = 5  # the pending page re-checks every few seconds, then asks the customer to reply "paid"


async def paid(request: Request) -> Response:
    """Paystack redirects here after checkout. Verify straight away so confirmation doesn't wait on the webhook."""
    reference = request.query_params.get("reference") or request.query_params.get("trxref")
    check = request.query_params.get("check", "0")
    checks = int(check) if check.isdigit() else 0
    with SessionLocal() as session:
        known = reference and session.scalar(select(Payment.id).where(Payment.reference == reference))
    if not reference:
        state = "missing"
    elif not known:
        state = "unknown"
    else:
        try:
            outcome = apply_and_log(reference, paystack.verify(reference), "callback")
        except WumiError:
            outcome = "unreachable"
        state = {
            "confirmed": "confirmed", "already_confirmed": "confirmed",
            "held": "held", "already_held": "held",
            "failed": "failed", "reversed": "failed", "abandoned": "abandoned",
        }.get(outcome, "pending")
    context = {"state": state, "reference": reference, **(details(reference) if known else {})}
    if state == "pending" and checks < MAX_CHECKS:
        context["refresh"] = f"/paid?reference={reference}&check={checks + 1}"
    return templates.TemplateResponse(request, "paid.html", context)


def details(reference: str) -> dict:
    """What the payment pages show about the payment and its order."""
    with SessionLocal() as session:
        pay = session.scalar(select(Payment).where(Payment.reference == reference))
        order = session.get(Order, pay.order_id)
        o = briefs.order_summary(session, order)
        name = session.get(Customer, order.customer_id).name.split()[0]
        return {
            "name": name,
            "o": o,
            "amount": naira(pay.amount_kobo),
            "charged": naira(pay.charged_kobo),
            "retry_url": pay.authorization_url,
            "owing": o["balance"] not in (None, "₦0"),
        }
