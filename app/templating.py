"""Jinja2 setup for the HTML pages (/v/... and /paid). Autoescape is on for every .html template."""

from datetime import date, datetime
from pathlib import Path

from starlette.templating import Jinja2Templates

from app.clock import LAGOS

templates = Jinja2Templates(directory=Path(__file__).parent / "templates")


def day(value: str | date | datetime | None) -> str:
    """'2026-10-28' -> '28 Oct 2026'."""
    if not value:
        return ""
    if isinstance(value, str):
        value = date.fromisoformat(value[:10])
    elif isinstance(value, datetime):
        value = (value.astimezone(LAGOS) if value.tzinfo else value).date()
    return f"{value.day} {value:%b %Y}"


def garments(items: list) -> str:
    """[{'colour': 'grey', 'type': 'senator outfit'}] -> 'Grey senator outfit'. Chat orders carry 'description'."""
    text = ", ".join(" ".join(x for x in (g.get("colour"), g.get("type")) if x) or g.get("description", "") for g in items)
    return text[:1].upper() + text[1:]


def status_label(status: str) -> str:
    return status.replace("_", " ").capitalize()


templates.env.filters.update(day=day, garments=garments, status_label=status_label)
