"""Background check of unpaid Paystack links. Our webhook URL can't be saved in Paystack, and /paid only runs if the
payer reaches the callback page, so this is what confirms a payment when the customer closes the tab."""

import asyncio
import logging
from collections import Counter
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.db import SessionLocal
from app.errors import WumiError
from app.integrations import paystack
from app.models import Payment
from app.services.payments import apply_and_log

logger = logging.getLogger("wumi.reconciler")

RECENT = timedelta(hours=24)  # checked every minute
OLDEST = timedelta(days=7)  # checked hourly
EVERY = 60


def _utc(ts: datetime) -> datetime:
    # SQLite returns naive UTC timestamps; Postgres returns aware ones.
    return ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts


def due_references(at: datetime, include_older: bool) -> list[str]:
    since = at - (OLDEST if include_older else RECENT)
    with SessionLocal() as session:
        rows = session.execute(
            select(Payment.reference, Payment.created_at).where(
                Payment.provider == "paystack",
                Payment.reference.is_not(None),
                # A failed attempt can be retried on the same link, so failed links stay in the check.
                Payment.status.in_(("pending", "failed")),
            )
        ).all()
    return [ref for ref, created in rows if _utc(created) >= since]


def run_once(at: datetime, include_older: bool) -> Counter:
    """Apply only successes, through the same path as the webhook. Never marks a link failed; skips it if Paystack errors."""
    counts: Counter = Counter()
    for reference in due_references(at, include_older):
        try:
            verified = paystack.verify(reference)
        except WumiError as exc:
            counts["skipped"] += 1
            logger.warning("reconciler %s skipped: %s", reference, exc.code)
            continue
        if verified.get("status") != "success":
            counts["unpaid"] += 1
            continue
        counts[apply_and_log(reference, verified, "reconciler")] += 1
    if counts or include_older:
        logger.info("reconciler pass (%s): %s", "7d" if include_older else "24h", dict(counts) or "nothing open")
    return counts


async def run_forever() -> None:
    logger.info("reconciler started: links under 24h every minute, up to 7 days hourly")
    last_older: datetime | None = None
    while True:
        at = datetime.now(timezone.utc)
        include_older = last_older is None or at - last_older >= timedelta(hours=1)
        try:
            await asyncio.to_thread(run_once, at, include_older)
            if include_older:
                last_older = at
        except Exception:
            logger.exception("reconciler pass failed")
        await asyncio.sleep(EVERY)
