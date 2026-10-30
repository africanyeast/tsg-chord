import hashlib
import hmac
import json
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from app.config import get_settings
from app.errors import WumiError
from app.integrations import paystack
from app.models import Order, Payment
from app.services import fixtures, reconciler
from app.tools import approve, check_payment, create_payment_link, get_my_orders, identify_customer, list_pending, request_order

SENDER = "customer_MONEY"
SIMI = "customer_SIMI"


@pytest.fixture(autouse=True)
def seeded(session, monkeypatch):
    fixtures.reset(session, on=date(2026, 10, 30))
    session.commit()
    monkeypatch.setenv("PAYSTACK_SECRET_KEY", "sk_test_unit")
    get_settings.cache_clear()
    calls = []
    monkeypatch.setattr(
        paystack, "initialize", lambda **kw: calls.append(kw) or f"https://checkout.test/{kw['reference']}"
    )
    yield calls
    get_settings.cache_clear()


def _requested() -> str:
    identify_customer(SENDER, "Amaka Okafor", "black corset gown")
    return request_order(SENDER, ["emerald lace gown"], "2026-12-12")["request_id"]


def _signed(body: dict) -> tuple[bytes, dict]:
    raw = json.dumps(body).encode()
    sig = hmac.new(b"sk_test_unit", raw, hashlib.sha512).hexdigest()
    return raw, {"x-paystack-signature": sig, "content-type": "application/json"}


def test_approve_twice_gives_one_order_and_one_link(session, seeded) -> None:
    req = _requested()
    first = approve(SIMI, "85k", "40,000", request_id=req)
    again = approve(SIMI, 85000, 40000, request_id=req)
    assert first["status"] == "approved" and first["deposit"] == "₦40,000"
    assert again["status"] == "already_done" and again["order_id"] == first["order_id"]
    assert len(seeded) == 1 and seeded[0]["amount_kobo"] == 4_000_000
    assert session.scalar(select(func.count()).select_from(Order).where(Order.customer_id == "CUS-001")) == 2
    view = get_my_orders(SENDER)["orders"][0]
    assert view["price"] == "₦85,000" and view["payment_link"]["url"].startswith("https://checkout.test/")


def test_bad_deposit_refused(seeded) -> None:
    req = _requested()
    assert approve(SIMI, "85k", "90k", request_id=req)["error"]["code"] == "VALIDATION_ERROR"
    assert len(seeded) == 0


def test_webhook_bad_signature_is_401(client) -> None:
    raw, headers = _signed({"event": "charge.success", "data": {"reference": "x", "amount": 1}})
    headers["x-paystack-signature"] = "0" * 128
    assert client.post("/webhooks/paystack", content=raw, headers=headers).status_code == 401


def test_webhook_confirms_once_and_replay_changes_nothing(client, session) -> None:
    ref = approve(SIMI, "85k", "40k", request_id=_requested())["reference"]
    raw, headers = _signed({"event": "charge.success", "data": {"reference": ref, "amount": 4_000_000}})
    assert client.post("/webhooks/paystack", content=raw, headers=headers).json()["outcome"] == "confirmed"
    assert client.post("/webhooks/paystack", content=raw, headers=headers).json()["outcome"] == "already_confirmed"
    session.expire_all()
    assert session.scalar(select(func.count()).select_from(Payment).where(Payment.status == "confirmed", Payment.reference == ref)) == 1
    assert check_payment(SENDER)["balance_now"] == "₦45,000"


def test_wrong_amount_is_held(client, session) -> None:
    ref = approve(SIMI, "85k", "40k", request_id=_requested())["reference"]
    raw, headers = _signed({"event": "charge.success", "data": {"reference": ref, "amount": 100}})
    assert client.post("/webhooks/paystack", content=raw, headers=headers).json()["outcome"] == "held"
    result = check_payment(SENDER)
    assert result["status"] == "already_held" and result["balance_now"] == "₦85,000"


def test_paid_claim_without_link_changes_nothing() -> None:
    identify_customer("customer_TUNDE", "Tunde Adebayo", "navy agbada")
    result = check_payment("customer_TUNDE")
    assert result["status"] == "no_payment_link"


def test_balance_link_refuses_more_than_balance(seeded) -> None:
    assert create_payment_link(SIMI, order_id="ORD-1001", amount="200k")["error"]["code"] == "VALIDATION_ERROR"
    ok = create_payment_link(SIMI, customer="Tunde", amount=None)
    assert ok["amount"] == "₦135,000" and len(seeded) == 1


def test_previous_order_still_verifies_after_a_new_one(seeded) -> None:
    approve(SIMI, "85k", "40k", request_id=_requested())
    assert identify_customer("customer_NEWCHAT", "Amaka Okafor", "black corset gown")["status"] == "identified"
    assert identify_customer("customer_OTHER", "Amaka Okafor", "emerald lace gown")["status"] == "identified"
    assert identify_customer("customer_WRONG", "Amaka Okafor", "blue agbada")["status"] == "not_verified"


def _down(*args, **kwargs):
    raise WumiError("UPSTREAM_ERROR", "Could not reach Paystack. Try again shortly.")


def test_paystack_down_on_approve_changes_nothing(session, monkeypatch) -> None:
    req = _requested()
    monkeypatch.setattr(paystack, "initialize", _down)
    assert approve(SIMI, "85k", "40k", request_id=req)["error"]["code"] == "UPSTREAM_ERROR"
    assert session.scalar(select(func.count()).select_from(Order).where(Order.customer_id == "CUS-001")) == 1
    assert list_pending(SIMI)["pending"][0]["request_id"] == req


def test_paystack_down_on_check_payment_changes_nothing(session, monkeypatch) -> None:
    ref = approve(SIMI, "85k", "40k", request_id=_requested())["reference"]
    monkeypatch.setattr(paystack, "verify", _down)
    assert check_payment(SENDER)["error"]["code"] == "UPSTREAM_ERROR"
    assert session.scalar(select(Payment.status).where(Payment.reference == ref)) == "pending"


def test_failed_payment_changes_nothing_and_says_so(client, monkeypatch) -> None:
    ref = approve(SIMI, "85k", "40k", request_id=_requested())["reference"]
    monkeypatch.setattr(paystack, "verify", lambda r: {"status": "failed", "amount": 4_000_000})
    assert "Payment didn’t go through" in client.get(f"/paid?reference={ref}").text
    result = check_payment(SENDER)
    assert result["status"] == "failed" and result["balance_now"] == "₦85,000"


def test_failed_then_retried_link_confirms(monkeypatch) -> None:
    approve(SIMI, "85k", "40k", request_id=_requested())
    monkeypatch.setattr(paystack, "verify", lambda r: {"status": "failed", "amount": 4_000_000})
    check_payment(SENDER)
    monkeypatch.setattr(paystack, "verify", lambda r: {"status": "success", "amount": 4_000_000})
    assert check_payment(SENDER)["status"] == "confirmed"


def test_older_link_paid_is_found(monkeypatch) -> None:
    deposit_ref = approve(SIMI, "85k", "40k", request_id=_requested())["reference"]
    create_payment_link(SIMI, customer="Amaka", amount="10k")
    monkeypatch.setattr(
        paystack, "verify", lambda r: {"status": "success" if r == deposit_ref else "abandoned", "amount": 4_000_000}
    )
    result = check_payment(SENDER)
    assert result["status"] == "confirmed" and result["amount"] == "₦40,000" and result["balance_now"] == "₦45,000"


def test_paying_two_links_for_one_balance_is_held(client, session) -> None:
    create_payment_link(SIMI, customer="Tunde", amount=None)
    create_payment_link(SIMI, customer="Tunde", amount=None)
    refs = session.scalars(select(Payment.reference).where(Payment.order_id == "ORD-1001", Payment.status == "pending")).all()
    outcomes = []
    for ref in refs:
        raw, headers = _signed({"event": "charge.success", "data": {"reference": ref, "amount": 13_500_000}})
        outcomes.append(client.post("/webhooks/paystack", content=raw, headers=headers).json()["outcome"])
    assert sorted(outcomes) == ["confirmed", "held"]


def test_webhook_for_unknown_reference_is_acknowledged(client) -> None:
    raw, headers = _signed({"event": "charge.success", "data": {"reference": "NOT-OURS", "amount": 100}})
    response = client.post("/webhooks/paystack", content=raw, headers=headers)
    assert response.status_code == 200 and response.json()["outcome"] == "NOT_FOUND"


# Fees and the reconciler (spec-v2 §19.1)


def test_fee_borne_by_customer_confirms(client, session) -> None:
    ref = approve(SIMI, "85k", "40k", request_id=_requested())["reference"]
    data = {"reference": ref, "amount": 4_060_000, "requested_amount": 4_000_000, "fees": 60_000}
    raw, headers = _signed({"event": "charge.success", "data": data})
    assert client.post("/webhooks/paystack", content=raw, headers=headers).json()["outcome"] == "confirmed"
    assert check_payment(SENDER)["balance_now"] == "₦45,000"


def test_verify_matches_requested_amount_with_fallback() -> None:
    with_fee = paystack.summary({"status": "success", "amount": 4_060_000, "requested_amount": 4_000_000, "fees": 60_000})
    assert with_fee["amount"] == 4_000_000 and with_fee["charged"] == 4_060_000
    assert paystack.summary({"status": "success", "amount": 4_000_000})["amount"] == 4_000_000


def _reconcile(**kw):
    return reconciler.run_once(datetime.now(timezone.utc), kw.get("include_older", False))


def test_reconciler_confirms_unpaid_link(session, monkeypatch) -> None:
    ref = approve(SIMI, "85k", "40k", request_id=_requested())["reference"]
    monkeypatch.setattr(paystack, "verify", lambda r: {"status": "success", "amount": 4_000_000, "charged": 4_000_000, "fees": 0})
    assert _reconcile()["confirmed"] == 1
    session.expire_all()
    assert session.scalar(select(Payment.status).where(Payment.reference == ref)) == "confirmed"


def test_reconciler_leaves_pending_link_alone(session, monkeypatch) -> None:
    ref = approve(SIMI, "85k", "40k", request_id=_requested())["reference"]
    for status in ("ongoing", "abandoned", "failed"):
        monkeypatch.setattr(paystack, "verify", lambda r, s=status: {"status": s, "amount": 4_000_000})
        assert _reconcile()["unpaid"] == 1
    session.expire_all()
    # Never marks a link failed: the customer can still pay it.
    assert session.scalar(select(Payment.status).where(Payment.reference == ref)) == "pending"


def test_reconciler_survives_paystack_down(session, monkeypatch) -> None:
    deposit = approve(SIMI, "85k", "40k", request_id=_requested())["reference"]
    create_payment_link(SIMI, customer="Tunde", amount=None)

    def verify(reference):
        if reference == deposit:
            return _down()
        return {"status": "success", "amount": 13_500_000}

    monkeypatch.setattr(paystack, "verify", verify)
    counts = _reconcile()
    assert counts["skipped"] == 1 and counts["confirmed"] == 1
    session.expire_all()
    assert session.scalar(select(Payment.status).where(Payment.reference == deposit)) == "pending"


def test_webhook_after_reconciler_changes_nothing(client, session, monkeypatch) -> None:
    ref = approve(SIMI, "85k", "40k", request_id=_requested())["reference"]
    monkeypatch.setattr(paystack, "verify", lambda r: {"status": "success", "amount": 4_000_000})
    _reconcile()
    raw, headers = _signed({"event": "charge.success", "data": {"reference": ref, "amount": 4_000_000}})
    assert client.post("/webhooks/paystack", content=raw, headers=headers).json()["outcome"] == "already_confirmed"
    assert check_payment(SENDER)["balance_now"] == "₦45,000"


def test_reconciler_checks_older_links_only_hourly(session, monkeypatch) -> None:
    ref = approve(SIMI, "85k", "40k", request_id=_requested())["reference"]
    payment = session.scalar(select(Payment).where(Payment.reference == ref))
    monkeypatch.setattr(paystack, "verify", lambda r: {"status": "ongoing", "amount": 4_000_000})
    payment.created_at = datetime.now(timezone.utc) - timedelta(days=3)
    session.commit()
    assert _reconcile() == {} and _reconcile(include_older=True)["unpaid"] == 1
    payment.created_at = datetime.now(timezone.utc) - timedelta(days=8)
    session.commit()
    assert _reconcile(include_older=True) == {}


def test_live_key_refuses_to_start(monkeypatch) -> None:
    monkeypatch.setenv("PAYSTACK_SECRET_KEY", "sk_live_secretvalue")
    get_settings.cache_clear()
    with pytest.raises(RuntimeError) as exc:
        get_settings()
    assert "secretvalue" not in str(exc.value)


def test_paid_page_says_what_happened(client, monkeypatch) -> None:
    ref = approve(SIMI, "85k", "40k", request_id=_requested())["reference"]
    page = lambda: client.get(f"/paid?reference={ref}").text
    monkeypatch.setattr(paystack, "verify", lambda r: {"status": "abandoned", "amount": 4_000_000})
    assert "Payment not completed" in page() and "Try again" in page()
    monkeypatch.setattr(paystack, "verify", lambda r: {"status": "ongoing", "amount": 4_000_000})
    assert "Confirming your payment" in page() and 'http-equiv="refresh"' in page()
    assert 'http-equiv="refresh"' not in client.get(f"/paid?reference={ref}&check=5").text
    monkeypatch.setattr(paystack, "verify", _down)
    assert "Confirming your payment" in page()
    monkeypatch.setattr(paystack, "verify", lambda r: {"status": "success", "amount": 4_000_000})
    t = page()
    assert "Payment received" in t and "₦40,000" in t and "Emerald lace gown" in t
    assert "Payment received" in page()  # already confirmed
    assert "couldn’t find this payment" in client.get("/paid?reference=nope").text
    assert "No payment to show" in client.get("/paid").text


def test_paid_page_held_shows_both_amounts(client, monkeypatch) -> None:
    ref = approve(SIMI, "85k", "40k", request_id=_requested())["reference"]
    monkeypatch.setattr(paystack, "verify", lambda r: {"status": "success", "amount": 4_100_000})
    text = client.get(f"/paid?reference={ref}").text
    assert "being checked" in text and "₦40,000" in text and "₦41,000" in text
