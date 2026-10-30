"""Live chat recording. Each simulator sender gets one live conversation."""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.clock import now
from app.errors import WumiError
from app.models import Conversation, Message
from app.services.customers import by_sender


def live_conversation(session: Session, sender_id: str, name: str | None) -> Conversation:
    conv = session.scalar(
        select(Conversation).where(Conversation.sender_id == sender_id, Conversation.source == "live")
    )
    if conv is None:
        count = session.scalar(select(func.count()).select_from(Conversation).where(Conversation.source == "live"))
        customer = by_sender(session, sender_id)
        conv = Conversation(
            id=f"live-{count + 1:03d}",
            customer_id=customer.id if customer else None,
            sender_id=sender_id,
            contact={"name": name, "sender_id": sender_id},
            source="live",
            started_at=now(),
        )
        session.add(conv)
        session.flush()
    return conv


def add_messages(session: Session, sender_id: str, messages: list[str], name: str | None) -> dict:
    texts = [m.strip() for m in messages if m and m.strip()]
    if not texts:
        raise WumiError("VALIDATION_ERROR", "No message text was given. Pass the customer's message verbatim.")
    conv = live_conversation(session, sender_id, name)
    seq = session.scalar(select(func.coalesce(func.max(Message.seq), 0)).where(Message.conversation_id == conv.id))
    ids = []
    for text in texts:
        seq += 1
        msg_id = f"MSG-{conv.id}-{seq:03d}"
        session.add(
            Message(
                id=msg_id,
                conversation_id=conv.id,
                seq=seq,
                ts=now(),
                sender="customer",
                type="text",
                text=text,
            )
        )
        ids.append(msg_id)
    return {"conversation_id": conv.id, "message_ids": ids}
