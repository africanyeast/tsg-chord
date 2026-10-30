from datetime import date

import pytest
from sqlalchemy import func, select

from app.models import PendingAction
from app.services import fixtures
from app.tools import add_conversation, get_my_orders, identify_customer, request_order

SENDER = "customer_TEST1"


@pytest.fixture(autouse=True)
def seeded(session):
    fixtures.reset(session, on=date(2026, 10, 30))
    session.commit()


def test_orders_hidden_until_verified() -> None:
    assert get_my_orders(SENDER)["error"]["code"] == "NOT_IDENTIFIED"

    first = identify_customer(SENDER, "Amaka")
    assert first["status"] == "needs_verification" and "customer_id" not in first

    assert identify_customer(SENDER, "Amaka", "a blue agbada")["status"] == "not_verified"
    assert get_my_orders(SENDER)["error"]["code"] == "NOT_IDENTIFIED"

    ok = identify_customer(SENDER, "Amaka", "the black corset gown")
    assert ok == {"status": "identified", "customer_id": "CUS-001", "name": "Amaka Okafor"}
    assert identify_customer(SENDER, "Amaka")["status"] == "identified"
    assert get_my_orders(SENDER)["orders"][0]["order_id"] == "ORD-1005"


def test_unknown_name_is_new_customer() -> None:
    assert identify_customer("customer_NEW", "Zainab Musa")["status"] == "new_customer"
    assert request_order("customer_NEW", "senator outfit")["error"]["code"] == "NOT_IDENTIFIED"
    ok = request_order("customer_NEW", ["senator outfit"], name="Zainab Musa", phone="0803 111 2222")
    assert ok["status"] == "requested"
    assert get_my_orders("customer_NEW")["requests"][0]["status"] == "waiting for Simi"


def test_request_creates_exactly_one_pending_action(session) -> None:
    identify_customer(SENDER, "Amaka Okafor", "black corset gown")
    ids = add_conversation(SENDER, "I need an outfit for a wedding on 2026-12-12", "Amaka Okafor")["message_ids"]
    first = request_order(SENDER, ["emerald lace gown"], "2026-12-12", "wedding guest", ids)
    again = request_order(SENDER, ["emerald lace gown"], "2026-12-12", "wedding guest", ids)
    assert first["status"] == "requested"
    assert again == {**first, "status": "already_requested"}
    rows = session.scalars(select(PendingAction).where(PendingAction.sender_id == SENDER)).all()
    assert len(rows) == 1 and rows[0].payload["source_message_ids"] == ids


def test_owner_tools_not_on_front_desk(client) -> None:
    def tool_names(path: str, token: str) -> set[str]:
        r = client.post(
            path,
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json, text/event-stream"},
        )
        assert r.status_code == 200, r.text
        return {t["name"] for t in r.json()["result"]["tools"]}

    assert tool_names("/mcp/", "test-front") == {
        "identify_customer",
        "add_conversation",
        "request_order",
        "get_my_orders",
        "check_payment",
        "add_measurements",
        "get_my_measurements",
    }
    assert "approve" not in tool_names("/mcp/", "test-front")
    assert tool_names("/mcp/owner/", "test-owner").isdisjoint({"request_order", "identify_customer"})
    deny = {"Authorization": "Bearer test-front", "Accept": "application/json, text/event-stream"}
    assert client.post("/mcp/owner/", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, headers=deny).status_code == 401
