"""Read-only pages Simi opens from links in the chat. Proof of concept: no token, ids only."""

from starlette.requests import Request
from starlette.responses import Response
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models import Customer, Order
from app.services import briefs
from app.services.payments import naira, paid_kobo
from app.templating import templates

DONE = ("delivered", "collected", "cancelled")


def card(session: Session, o: Order) -> dict:
    """order_summary plus what the pages show: share paid, fitting and notes."""
    s = briefs.order_summary(session, o)
    paid = paid_kobo(session, o.id)
    s["pct_paid"] = min(100, round(paid * 100 / o.price_kobo)) if o.price_kobo else 0
    s["owing"] = s["balance"] not in (None, "₦0")
    s["fitting_date"] = o.fitting_date
    s["notes"] = o.notes
    return s


def not_found(request: Request, what: str) -> Response:
    return templates.TemplateResponse(request, "not_found.html", {"what": what}, status_code=404)


async def customer(request: Request) -> Response:
    with SessionLocal() as s:
        c = s.get(Customer, request.path_params["id"])
        if c is None:
            return not_found(request, "Customer")
        p = briefs.profile_of(s, c)
        p["email"] = c.email
        orders = [card(s, s.get(Order, o["order_id"])) for o in p["orders"]]
        total_paid = sum(paid_kobo(s, o["order_id"]) for o in orders)
    # Per body part, every reading newest first: the first is current, the rest are history.
    readings: dict[str, list[dict]] = {}
    for m in reversed(p["measurements"]["history"]):  # oldest first keeps the parts in their usual order
        for part, value in m["values"].items():
            readings.setdefault(part, []).insert(0, {"value": value, "unit": m["unit"], "taken_on": m["taken_on"]})
    return templates.TemplateResponse(request, "customer.html", {
        "p": p,
        "orders": orders,
        "owing": p["total_balance"] != "₦0",
        "total_paid": naira(total_paid),
        "open_orders": sum(o["status"] not in DONE for o in orders),
        "readings": readings,
    })


async def order(request: Request) -> Response:
    with SessionLocal() as s:
        o = s.get(Order, request.path_params["id"])
        if o is None:
            return not_found(request, "Order")
        summary = card(s, o)
        payments = briefs.order_payments(s, o)
    return templates.TemplateResponse(request, "order.html", {"o": summary, "payments": payments})
