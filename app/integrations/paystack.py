"""Paystack test-mode client: initialize, verify, and webhook signature check. Amounts are kobo."""

import hashlib
import hmac

import httpx

from app.config import get_settings
from app.errors import WumiError

BASE = "https://api.paystack.co"


def _client() -> httpx.Client:
    key = get_settings().paystack_secret_key
    if not key:
        raise WumiError("UPSTREAM_ERROR", "Payments are not configured on the server.")
    return httpx.Client(base_url=BASE, headers={"Authorization": f"Bearer {key}"}, timeout=15)


def _data(response: httpx.Response) -> dict:
    try:
        body = response.json()
    except ValueError:
        body = {}
    if response.status_code >= 400 or not body.get("status"):
        raise WumiError("UPSTREAM_ERROR", f"Paystack said: {body.get('message') or response.status_code}")
    return body["data"]


def initialize(email: str, amount_kobo: int, reference: str, callback_url: str, metadata: dict) -> str:
    """Create a checkout and return its authorization URL."""
    try:
        with _client() as client:
            data = _data(
                client.post(
                    "/transaction/initialize",
                    json={
                        "email": email,
                        "amount": amount_kobo,
                        "reference": reference,
                        "callback_url": callback_url,
                        "metadata": metadata,
                    },
                )
            )
    except httpx.HTTPError as exc:
        raise WumiError("UPSTREAM_ERROR", "Could not reach Paystack. Try again shortly.") from exc
    return data["authorization_url"]


def summary(data: dict) -> dict:
    """A transaction as the ledger sees it. When the customer bears the fee, Paystack's `amount` includes it
    and `requested_amount` is what the link asked for, so the ledger matches on `requested_amount`."""
    requested = data.get("requested_amount")
    return {
        "status": data.get("status"),
        "amount": requested if requested is not None else data.get("amount"),
        "charged": data.get("amount"),
        "fees": data.get("fees"),
    }


def verify(reference: str) -> dict:
    """Returns summary(): {'status': success|failed|abandoned|ongoing|pending|..., 'amount': kobo, ...}."""
    try:
        with _client() as client:
            data = _data(client.get(f"/transaction/verify/{reference}"))
    except httpx.HTTPError as exc:
        raise WumiError("UPSTREAM_ERROR", "Could not reach Paystack. Try again shortly.") from exc
    return summary(data)


def valid_signature(body: bytes, signature: str | None) -> bool:
    key = get_settings().paystack_secret_key
    if not key or not signature:
        return False
    expected = hmac.new(key.encode(), body, hashlib.sha512).hexdigest()
    return hmac.compare_digest(expected, signature)
