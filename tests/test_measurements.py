from datetime import date

import pytest
from app.config import get_settings
from app.integrations import paystack
from app.services import fixtures
from app.tools import (
    add_customer_measurements,
    add_measurements,
    approve,
    get_brief,
    get_my_measurements,
    identify_customer,
    request_order,
)

AMAKA = "customer_AMAKA"
SIMI = "customer_SIMI"


@pytest.fixture(autouse=True)
def seeded(session, monkeypatch):
    fixtures.reset(session, on=date(2026, 10, 30))
    session.commit()
    monkeypatch.setenv("PAYSTACK_SECRET_KEY", "sk_test_unit")
    get_settings.cache_clear()
    monkeypatch.setattr(paystack, "initialize", lambda **kw: f"https://checkout.test/{kw['reference']}")
    yield
    get_settings.cache_clear()


def _verified() -> None:
    assert identify_customer(AMAKA, "Amaka Okafor", "black corset gown")["status"] == "identified"


def test_measurements_hidden_and_refused_until_verified() -> None:
    assert get_my_measurements(AMAKA)["error"]["code"] == "NOT_IDENTIFIED"
    assert add_measurements(AMAKA, "waist 30", "in")["error"]["code"] == "NOT_IDENTIFIED"


def test_current_is_latest_value_per_part() -> None:
    _verified()
    mine = get_my_measurements(AMAKA)
    assert mine["current"]["waist"]["value"] == 31 and mine["current"]["bust"]["value"] == 38


def test_correction_adds_a_set_and_keeps_history(session) -> None:
    _verified()
    result = add_measurements(AMAKA, "Waist 30.5, hip 42", "inches")
    assert result["status"] == "recorded" and result["recorded"]["values"] == {"waist": 30.5, "hip": 42}
    assert result["current"]["waist"]["value"] == 30.5 and result["current"]["bust"]["value"] == 38
    assert len(result["history"]) == 3 and result["big_changes"] == []


def test_unit_is_asked_for_not_guessed() -> None:
    _verified()
    assert add_measurements(AMAKA, {"waist": 30})["error"]["code"] == "UNIT_NEEDED"
    assert add_measurements(AMAKA, {"waist": 30}, "feet")["error"]["code"] == "VALIDATION_ERROR"


def test_unbelievable_and_unreadable_values_refused() -> None:
    _verified()
    assert add_measurements(AMAKA, {"waist": 300}, "in")["error"]["code"] == "VALIDATION_ERROR"
    assert add_measurements(AMAKA, {"waist": "thirty"}, "in")["error"]["code"] == "VALIDATION_ERROR"
    assert add_measurements(AMAKA, "no numbers here", "in")["error"]["code"] == "VALIDATION_ERROR"


def test_big_change_is_flagged_for_confirmation() -> None:
    _verified()
    result = add_measurements(AMAKA, {"waist": 40}, "in")
    assert result["big_changes"][0]["part"] == "waist" and result["big_changes"][0]["was"] == 31


def test_new_customer_measurements_move_to_record_on_approval(session) -> None:
    req = request_order("customer_CHIDI", ["cream senator"], name="Chidi Eze", phone="08105394287")["request_id"]
    held = add_measurements("customer_CHIDI", "chest 42, sleeve 24", "in")
    assert held["status"] == "recorded_with_request"
    assert approve(SIMI, "60k", "20k", request_id=req)["status"] == "approved"
    assert get_my_measurements("customer_CHIDI")["current"]["chest"]["value"] == 42


def test_simi_records_for_a_customer() -> None:
    result = add_customer_measurements(SIMI, "Tunde", {"chest": 45}, "in", taken_on="2026-10-29")
    assert result["status"] == "recorded" and result["current"]["chest"] == {"value": 45, "unit": "in", "taken_on": "2026-10-29"}


def test_brief_flags_open_orders_needing_measurements() -> None:
    req = request_order("customer_CHIDI", ["cream senator"], name="Chidi Eze", phone="08105394287")["request_id"]
    approve(SIMI, "60k", "20k", request_id=req)
    needed = {m["customer"]: m["problem"] for m in get_brief(SIMI)["measurements_needed"]}
    assert needed == {"Mrs Adeyemi": "last measured 300 days ago", "Mrs Juwon": "no measurements on record", "Chidi Eze": "no measurements on record"}
