"""Business messaging (architecture doc's Phase 3 "WhatsApp Business
integration" and "SMS by regulated market" - two channels, one Conversation/
Message shape, per the email spec's MSG domain). Both channels gate on a
per-number, out-of-band-approved flag (whatsapp_enabled / sms_enabled) -
real WhatsApp Business senders are approved by Meta/Twilio per number, and
real US SMS business messaging requires A2P 10DLC brand/campaign
registration; this app only records that approval happened, the same
pattern ai_receptionist_enabled already uses for a different capability.
"""

from datetime import datetime

from sqlalchemy.orm import Session

from app.audit.service import log_event
from app.integrations.telecom import twilio as telecom
from app.messaging.models import Conversation, Message, MessageDirection, MessageStatus, MessagingChannel
from app.numbering.numbers.models import PhoneNumber

_OPT_OUT_KEYWORDS = {"stop", "unsubscribe", "cancel", "end", "quit"}
_OPT_IN_KEYWORDS = {"start", "unstop", "subscribe"}


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
    log_event(db, actor_id=actor_id, action=f"messaging.{channel.value}.sent", target_type="messaging_conversation",
               target_id=conversation.id, metadata={"to": to, "message_id": message.id})
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
        conversation.opted_out = True
        conversation.opted_out_at = datetime.utcnow()
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
    log_event(db, actor_id=number.account_id, action=f"messaging.{channel.value}.received", target_type="messaging_conversation",
               target_id=conversation.id, metadata={"from": from_number})
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
    try:
        message.status = MessageStatus(status)
    except ValueError:
        return
    db.commit()


def list_conversations(db: Session, account_id: str) -> list[Conversation]:
    return (
        db.query(Conversation)
        .filter(Conversation.account_id == account_id)
        .order_by(Conversation.last_message_at.desc())
        .all()
    )


def list_messages(db: Session, account_id: str, conversation_id: str) -> list[Message]:
    conversation = (
        db.query(Conversation)
        .filter(Conversation.id == conversation_id, Conversation.account_id == account_id)
        .first()
    )
    if conversation is None:
        raise ConversationNotFoundError(conversation_id)
    return db.query(Message).filter(Message.conversation_id == conversation_id).order_by(Message.created_at.asc()).all()
