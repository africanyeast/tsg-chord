"""Database schema. Money is integer kobo; balances are derived from confirmed payments, never stored."""

from datetime import date, datetime

from sqlalchemy import JSON, Date, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class Customer(Base):
    __tablename__ = "customers"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    phone: Mapped[str | None] = mapped_column(String(32))
    email: Mapped[str | None] = mapped_column(String(200))
    preferences: Mapped[list] = mapped_column(JSON, default=list)
    notes: Mapped[str | None] = mapped_column(Text)
    # Simulator sender ID, linked only after the server verifies the customer.
    sender_id: Mapped[str | None] = mapped_column(String(128), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Measurement(Base):
    __tablename__ = "measurements"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    customer_id: Mapped[str] = mapped_column(ForeignKey("customers.id"), index=True)
    label: Mapped[str] = mapped_column(String(200))
    unit: Mapped[str | None] = mapped_column(String(8))
    values: Mapped[dict] = mapped_column(JSON)
    taken_on: Mapped[date]
    recorded_by: Mapped[str | None] = mapped_column(String(16))  # seed | customer | simi
    source_message_ids: Mapped[list | None] = mapped_column(JSON)


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    customer_id: Mapped[str | None] = mapped_column(ForeignKey("customers.id"), index=True)
    sender_id: Mapped[str | None] = mapped_column(String(128), index=True)
    contact: Mapped[dict] = mapped_column(JSON, default=dict)
    source: Mapped[str] = mapped_column(String(16))  # seed | live
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(String(96), primary_key=True)  # MSG-<conversation>-NNN
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), index=True)
    seq: Mapped[int]
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    sender: Mapped[str] = mapped_column(String(16))  # customer | owner
    type: Mapped[str] = mapped_column(String(32))
    text: Mapped[str | None] = mapped_column(Text)
    media_ref: Mapped[str | None] = mapped_column(String(64))


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    customer_id: Mapped[str] = mapped_column(ForeignKey("customers.id"), index=True)
    garments: Mapped[list] = mapped_column(JSON)
    price_kobo: Mapped[int | None]
    deposit_kobo: Mapped[int | None]
    status: Mapped[str] = mapped_column(String(32))
    event_date: Mapped[date | None] = mapped_column(Date)
    due_date: Mapped[date | None] = mapped_column(Date)
    fitting_date: Mapped[date | None] = mapped_column(Date)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), index=True)
    # Amount asked for (a link) or recorded; only status=confirmed counts toward the balance.
    amount_kobo: Mapped[int]
    status: Mapped[str] = mapped_column(String(24))  # pending | confirmed | held | failed | abandoned | claimed_unconfirmed
    provider: Mapped[str | None] = mapped_column(String(24))
    reference: Mapped[str | None] = mapped_column(String(96), unique=True)
    authorization_url: Mapped[str | None] = mapped_column(String(512))
    charged_kobo: Mapped[int | None]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PendingAction(Base):
    __tablename__ = "pending_actions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    kind: Mapped[str] = mapped_column(String(32))  # order_request
    customer_id: Mapped[str | None] = mapped_column(ForeignKey("customers.id"), index=True)
    sender_id: Mapped[str | None] = mapped_column(String(128))
    payload: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(16), index=True)  # pending | approved | rejected
    result: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Event(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    endpoint: Mapped[str] = mapped_column(String(16))  # front | owner | admin | webhook
    tool: Mapped[str] = mapped_column(String(64))
    outcome: Mapped[str] = mapped_column(String(32))
    detail: Mapped[dict | None] = mapped_column(JSON)


class OwnerLink(Base):
    """The simulator sender ID of Simi's own chat. Kept across /admin/reset, like the owner's phone would be."""

    __tablename__ = "owner_links"

    sender_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    linked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
