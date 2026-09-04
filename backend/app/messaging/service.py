"""Business messaging (architecture doc's Phase 3 "WhatsApp Business
integration" and "SMS by regulated market" - two channels, one Conversation/
Message shape, per the email spec's MSG domain). Both channels gate on a
per-number, out-of-band-approved flag (whatsapp_enabled / sms_enabled) -
real WhatsApp Business senders are approved by Meta/Twilio per number, and
real US SMS business messaging requires A2P 10DLC brand/campaign
registration; this app only records that approval happened, the same
pattern ai_receptionist_enabled already uses for a different capability.
"""

import logging
from datetime import datetime

from sqlalchemy.orm import Session

from app.audit.service import log_event
from app.billing import service as billing_service
from app.events.service import publish_message_received, publish_message_sent
from app.integrations.cache.redis import cache_delete, cache_get, cache_set
from app.integrations.telecom import twilio as telecom
from app.messaging.models import Conversation, Message, MessageDirection, MessageStatus, MessagingChannel
from app.notifications.service import (
    notify_inbound_message_received,
    notify_message_delivery_failed,
    notify_recipient_opted_out,
)
from app.numbering.identity.models import User, UserRole
from app.numbering.numbers.models import PhoneNumber

_OPT_OUT_KEYWORDS = {"stop", "unsubscribe", "cancel", "end", "quit"}
_OPT_IN_KEYWORDS = {"start", "unstop", "subscribe"}

logger = logging.getLogger("zoiko.messaging")

# Twilio's real status-callback value for a message that failed to reach
# the handset - this app's MessageStatus enum never had it, so it silently
# fell through update_message_status's ValueError below with no logging,
# leaving the message stuck at its previous status (e.g. "sent") forever,
# indistinguishable from a real success.
_STATUS_ALIASES = {"undelivered": MessageStatus.FAILED}


class NumberNotOwnedError(Exception):
    pass


class ChannelNotEnabledError(Exception):
    pass


class RecipientOptedOutError(Exception):
    pass


class ConversationNotFoundError(Exception):
    pass


def _channel_enabled(number: PhoneNumber, channel: MessagingChannel) -> bool:
    return number.whatsapp_enabled if channel == MessagingChannel.WHATSAPP else number.sms_enabled


def _get_owned_number_for_channel(db: Session, account_id: str, phone_number_id: str, channel: MessagingChannel) -> PhoneNumber:
    number = (
        db.query(PhoneNumber)
        .filter(PhoneNumber.id == phone_number_id, PhoneNumber.account_id == account_id)
        .first()
    )
    if number is None:
        raise NumberNotOwnedError(phone_number_id)
    if not _channel_enabled(number, channel):
        raise ChannelNotEnabledError(phone_number_id)
    return number


def _get_or_create_conversation(
    db: Session, account_id: str, phone_number_id: str, customer_number: str, channel: MessagingChannel
) -> Conversation:
    conversation = (
        db.query(Conversation)
        .filter(
            Conversation.phone_number_id == phone_number_id,
            Conversation.customer_number == customer_number,
            Conversation.channel == channel,
        )
        .first()
    )
    if conversation is None:
        conversation = Conversation(
            account_id=account_id, phone_number_id=phone_number_id, customer_number=customer_number, channel=channel,
        )
        db.add(conversation)
        db.commit()
    return conversation


def _send_via_provider(channel: MessagingChannel, to: str, from_number: str, body: str) -> dict:
    if channel == MessagingChannel.WHATSAPP:
        return telecom.send_whatsapp_message(to=to, from_number=from_number, body=body)
    return telecom.send_customer_sms(to=to, from_number=from_number, body=body)


def send_message(
    db: Session, account_id: str, actor_id: str, phone_number_id: str, to: str, body: str, channel: MessagingChannel
) -> Message:
    # ZL-COM-ENT-001 v3.0 - plan-tier gate, additive to (not a replacement
    # for) the per-number whatsapp_enabled/sms_enabled approval check below.
    billing_service.assert_entitlement(db, account_id, "messaging.enabled")
    number = _get_owned_number_for_channel(db, account_id, phone_number_id, channel)
    conversation = _get_or_create_conversation(db, account_id, phone_number_id, to, channel)
    if conversation.opted_out:
        raise RecipientOptedOutError(to)

    result = _send_via_provider(channel, to, number.e164, body)

    message = Message(
        conversation_id=conversation.id, direction=MessageDirection.OUTBOUND, body=body,
        provider_message_sid=result["sid"], status=MessageStatus.QUEUED,
    )
    db.add(message)
    conversation.last_message_at = datetime.utcnow()
    db.commit()
    _invalidate_conversations_cache(account_id)
    _invalidate_messages_cache(conversation.id)
    log_event(db, actor_id=actor_id, action=f"messaging.{channel.value}.sent", target_type="messaging_conversation",
               target_id=conversation.id, metadata={"to": to, "message_id": message.id})
    publish_message_sent(account_id, message_id=message.id, conversation_id=conversation.id, channel=channel.value)
    return message


def _record_inbound(
    db: Session, to_number: str, from_number: str, body: str, provider_message_sid: str, channel: MessagingChannel
) -> Message | None:
    if channel == MessagingChannel.WHATSAPP:
        column_filter = PhoneNumber.whatsapp_enabled.is_(True)
    else:
        column_filter = PhoneNumber.sms_enabled.is_(True)
    number = db.query(PhoneNumber).filter(PhoneNumber.e164 == to_number, column_filter).first()
    if number is None:
        return None

    conversation = _get_or_create_conversation(db, number.account_id, number.id, from_number, channel)

    normalized = body.strip().lower()
    if normalized in _OPT_OUT_KEYWORDS:
        newly_opted_out = not conversation.opted_out
        conversation.opted_out = True
        conversation.opted_out_at = datetime.utcnow()
        if newly_opted_out:
            owner = db.query(User).filter(User.account_id == number.account_id, User.role == UserRole.OWNER).first()
            if owner is not None:
                notify_recipient_opted_out(
                    db, account_id=number.account_id, account_email=owner.email,
                    destination_masked=from_number, sender_summary=f"{channel.value} messaging on {number.e164}",
                )
    elif normalized in _OPT_IN_KEYWORDS:
        conversation.opted_out = False
        conversation.opted_out_at = None

    message = Message(
        conversation_id=conversation.id, direction=MessageDirection.INBOUND, body=body,
        provider_message_sid=provider_message_sid, status=MessageStatus.RECEIVED,
    )
    db.add(message)
    conversation.last_message_at = datetime.utcnow()
    db.commit()
    _invalidate_conversations_cache(number.account_id)
    _invalidate_messages_cache(conversation.id)
    log_event(db, actor_id=number.account_id, action=f"messaging.{channel.value}.received", target_type="messaging_conversation",
               target_id=conversation.id, metadata={"from": from_number})
    publish_message_received(number.account_id, message_id=message.id, conversation_id=conversation.id, channel=channel.value)

    owner = db.query(User).filter(User.account_id == number.account_id, User.role == UserRole.OWNER).first()
    if owner is not None:
        notify_inbound_message_received(
            db, account_id=number.account_id, account_email=owner.email, e164=number.e164, from_number=from_number,
        )

    return message


def record_inbound_whatsapp_message(db: Session, whatsapp_to: str, whatsapp_from: str, body: str, provider_message_sid: str) -> Message | None:
    """Twilio's inbound WhatsApp webhook - `whatsapp_to`/`whatsapp_from`
    still carry the `whatsapp:` scheme prefix Twilio sends them with."""
    return _record_inbound(
        db, whatsapp_to.removeprefix("whatsapp:"), whatsapp_from.removeprefix("whatsapp:"),
        body, provider_message_sid, MessagingChannel.WHATSAPP,
    )


def record_inbound_sms_message(db: Session, to_number: str, from_number: str, body: str, provider_message_sid: str) -> Message | None:
    return _record_inbound(db, to_number, from_number, body, provider_message_sid, MessagingChannel.SMS)


def update_message_status(db: Session, provider_message_sid: str, status: str) -> None:
    message = db.query(Message).filter(Message.provider_message_sid == provider_message_sid).first()
    if message is None:
        return
    if status in _STATUS_ALIASES:
        message.status = _STATUS_ALIASES[status]
    else:
        try:
            message.status = MessageStatus(status)
        except ValueError:
            logger.warning(
                "Unrecognized Twilio message status %r for message %s (provider_message_sid=%s) - ignoring.",
                status, message.id, provider_message_sid,
            )
            return
    db.commit()
    _invalidate_messages_cache(message.conversation_id)

    if message.status == MessageStatus.FAILED:
        conversation = db.query(Conversation).filter(Conversation.id == message.conversation_id).first()
        if conversation is not None:
            owner = db.query(User).filter(User.account_id == conversation.account_id, User.role == UserRole.OWNER).first()
            if owner is not None:
                notify_message_delivery_failed(
                    db, account_id=conversation.account_id, account_email=owner.email,
                    message_reference=message.id, destination=conversation.customer_number, failure_category=status,
                )


def _conversations_cache_key(account_id: str) -> str:
    return f"conversations:list:{account_id}"


# Same short-TTL, invalidate-on-write pattern as media.service's calls/
# voicemails/video-sessions caches - invalidated at both write paths that
# change a field this list reflects (last_message_at on every message,
# opted_out/opted_out_at on an inbound STOP/START).
_CONVERSATIONS_CACHE_TTL_SECONDS = 15


def _serialize_conversation(c: Conversation) -> dict:
    return {
        "id": c.id,
        "account_id": c.account_id,
        "phone_number_id": c.phone_number_id,
        "customer_number": c.customer_number,
        "channel": c.channel.value,
        "opted_out": c.opted_out,
        "opted_out_at": c.opted_out_at.isoformat() if c.opted_out_at else None,
        "last_message_at": c.last_message_at.isoformat() if c.last_message_at else None,
        "created_at": c.created_at.isoformat() if c.created_at else None,
    }


def _deserialize_conversation(data: dict) -> Conversation:
    return Conversation(
        id=data["id"],
        account_id=data["account_id"],
        phone_number_id=data["phone_number_id"],
        customer_number=data["customer_number"],
        channel=MessagingChannel(data["channel"]),
        opted_out=data["opted_out"],
        opted_out_at=datetime.fromisoformat(data["opted_out_at"]) if data["opted_out_at"] else None,
        last_message_at=datetime.fromisoformat(data["last_message_at"]) if data["last_message_at"] else None,
        created_at=datetime.fromisoformat(data["created_at"]) if data["created_at"] else None,
    )


def _invalidate_conversations_cache(account_id: str) -> None:
    cache_delete(_conversations_cache_key(account_id))


def list_conversations(db: Session, account_id: str) -> list[Conversation]:
    cache_key = _conversations_cache_key(account_id)
    cached = cache_get(cache_key)
    if cached is not None:
        return [_deserialize_conversation(row) for row in cached]
    conversations = (
        db.query(Conversation)
        .filter(Conversation.account_id == account_id)
        .order_by(Conversation.last_message_at.desc())
        .all()
    )
    cache_set(cache_key, [_serialize_conversation(c) for c in conversations], ttl_seconds=_CONVERSATIONS_CACHE_TTL_SECONDS)
    return conversations


def _messages_cache_key(conversation_id: str) -> str:
    return f"messages:list:{conversation_id}"


_MESSAGES_CACHE_TTL_SECONDS = 15


def _serialize_message(m: Message) -> dict:
    return {
        "id": m.id,
        "conversation_id": m.conversation_id,
        "direction": m.direction.value,
        "body": m.body,
        "provider_message_sid": m.provider_message_sid,
        "status": m.status.value,
        "created_at": m.created_at.isoformat() if m.created_at else None,
    }


def _deserialize_message(data: dict) -> Message:
    return Message(
        id=data["id"],
        conversation_id=data["conversation_id"],
        direction=MessageDirection(data["direction"]),
        body=data["body"],
        provider_message_sid=data["provider_message_sid"],
        status=MessageStatus(data["status"]),
        created_at=datetime.fromisoformat(data["created_at"]) if data["created_at"] else None,
    )


def _invalidate_messages_cache(conversation_id: str) -> None:
    cache_delete(_messages_cache_key(conversation_id))


def list_messages(db: Session, account_id: str, conversation_id: str) -> list[Message]:
    conversation = (
        db.query(Conversation)
        .filter(Conversation.id == conversation_id, Conversation.account_id == account_id)
        .first()
    )
    if conversation is None:
        raise ConversationNotFoundError(conversation_id)

    cache_key = _messages_cache_key(conversation_id)
    cached = cache_get(cache_key)
    if cached is not None:
        return [_deserialize_message(row) for row in cached]
    messages = db.query(Message).filter(Message.conversation_id == conversation_id).order_by(Message.created_at.asc()).all()
    cache_set(cache_key, [_serialize_message(m) for m in messages], ttl_seconds=_MESSAGES_CACHE_TTL_SECONDS)
    return messages
