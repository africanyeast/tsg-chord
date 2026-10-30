import json
from datetime import date

from sqlalchemy import func, select
from app.models import Customer, Message, Order
from app.services import fixtures
from app.services.payments import balance_kobo, paid_kobo, payment_state

SEED = json.loads((fixtures.FIXTURES / "seed.json").read_text())
DAY = date(2026, 10, 30)


def test_derived_balances_match_seed(session) -> None:
    fixtures.reset(session, on=DAY)
    for expected in SEED["orders"]:
        order = session.get(Order, expected["id"])
        assert paid_kobo(session, order.id) == expected["paid_kobo"], order.id
        assert balance_kobo(session, order) == expected["price_kobo"] - expected["paid_kobo"], order.id
        assert payment_state(session, order, on=DAY) == expected["payment_state"], order.id


def test_dates_rebased_and_messages_numbered(session) -> None:
    fixtures.reset(session, on=DAY)
    assert session.get(Order, "ORD-1001").due_date == date(2026, 10, 26)
    first = session.get(Message, "MSG-conv-001-001")
    assert first.sender == "customer" and first.conversation_id == "conv-001"
    assert session.scalar(select(func.count()).select_from(Message)) > 0


def test_reset_clears_live_links(session) -> None:
    fixtures.reset(session, on=DAY)
    session.get(Customer, "CUS-001").sender_id = "customer_abc"
    session.commit()
    fixtures.reset(session, on=DAY)
    assert session.get(Customer, "CUS-001").sender_id is None


def test_admin_routes_need_token(client) -> None:
    assert client.post("/admin/reset").status_code == 401
    ok = client.post("/admin/reset", headers={"Authorization": "Bearer test-admin"})
    assert ok.status_code == 200 and ok.json()["loaded"]["orders"] == 19
    logs = client.get("/admin/logs", headers={"Authorization": "Bearer test-admin"}).json()
    assert {o["id"]: o["payment_state"] for o in logs["orders"]}["ORD-1001"] == "overdue"
    assert logs["events"][0]["tool"] == "reset"


def test_view_pages_and_brief_overview(client) -> None:
    client.post("/admin/reset", headers={"Authorization": "Bearer test-admin"})
    page = client.get("/v/customer/CUS-003")
    assert page.status_code == 200 and "Tunde Adebayo" in page.text and "Navy agbada" in page.text and "Phone" in page.text and "tunde" in page.text.lower()
    assert "Before: 30 in" in client.get("/v/customer/CUS-001").text
    order = client.get("/v/order/ORD-1001").text
    assert "agbada" in order and "Payments" in order
    assert client.get("/v/order/NOPE").status_code == 404
    from app.tools import get_brief

    brief = get_brief("customer_SIMI")
    assert brief["overview"]["active_orders"] > brief["overview"]["needing_attention"] > 0
    assert brief["overdue_balances"][0]["customer_url"].endswith("/v/customer/CUS-003")
