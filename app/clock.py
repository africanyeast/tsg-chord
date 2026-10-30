"""Business time. Lagos has no DST, so a fixed UTC+1 offset avoids depending on the image's tz database."""

from datetime import date, datetime, timedelta, timezone

LAGOS = timezone(timedelta(hours=1), "WAT")


def now() -> datetime:
    return datetime.now(LAGOS)


def today() -> date:
    return now().date()
