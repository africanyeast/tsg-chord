"""Admin routes: reset to seed data, and recent logs plus the derived order state."""

import hmac

from sqlalchemy import select
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, Response

from app.clock import today
from app.config import get_settings
from app.db import SessionLocal
from app.models import Event, Order, PendingAction
from app.services import fixtures
from app.services.payments import balance_kobo, days_overdue, paid_kobo, payment_state


def _unauthorized(request: Request) -> Response | None:
    token = get_settings().admin_bearer_token
    if not token:
        return PlainTextResponse("Admin bearer token is not configured", status_code=503)
    if not hmac.compare_digest(request.headers.get("authorization", ""), f"Bearer {token}"):
        return PlainTextResponse("Unauthorized", status_code=401)
    return None


async def reset(request: Request) -> Response:
    if denied := _unauthorized(request):
        return denied
    with SessionLocal() as session, session.begin():
        counts = fixtures.reset(session)
    return JSONResponse({"status": "ok", "reset_date": today().isoformat(), "loaded": counts})


async def logs(request: Request) -> Response:
    if denied := _unauthorized(request):
        return denied
    limit = min(int(request.query_params.get("limit", 50)), 500)
    with SessionLocal() as session:
        events = session.scalars(select(Event).order_by(Event.id.desc()).limit(limit)).all()
        orders = session.scalars(select(Order).order_by(Order.id)).all()
        pending = session.scalars(select(PendingAction).order_by(PendingAction.created_at)).all()
        return JSONResponse(
            {
                "today": today().isoformat(),
                "events": [
                    {
                        "ts": e.ts.isoformat(),
                        "endpoint": e.endpoint,
                        "tool": e.tool,
                        "outcome": e.outcome,
                        "detail": e.detail,
                    }
                    for e in events
                ],
                "orders": [
                    {
                        "id": o.id,
                        "customer_id": o.customer_id,
                        "status": o.status,
                        "price_kobo": o.price_kobo,
                        "paid_kobo": paid_kobo(session, o.id),
                        "balance_kobo": balance_kobo(session, o),
                        "due_date": o.due_date.isoformat() if o.due_date else None,
                        "days_overdue": days_overdue(o),
                        "payment_state": payment_state(session, o),
                    }
                    for o in orders
                ],
                "pending_actions": [
                    {"id": p.id, "kind": p.kind, "customer_id": p.customer_id, "status": p.status} for p in pending
                ],
            }
        )
