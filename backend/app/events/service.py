import logging
import uuid
from datetime import datetime, timezone

from opentelemetry import metrics

from app.integrations.eventbus import kafka as eventbus
from app.integrations.eventbus.kafka import EventBusError

logger = logging.getLogger("zoiko.events")

_meter = metrics.get_meter("zoiko.events")
_publish_failures = _meter.create_counter(
    "zoiko.events.publish_failures", description="Kafka publish failures - best-effort, never fails the caller"
)


def publish_event(topic: str, event_type: str, account_id: str | None, data: dict) -> None:
    """The one pipeline every domain event goes through - mirrors
    notifications.service.send_notification's role for the email/SMS/push
    pipeline. Best-effort: a Kafka outage must never fail the business
    transaction that's already committed to Postgres: the event bus is a
    durable/replaayable *record* of what happened, not the system of record
    for whether it happened."""
    envelope = {
        "event_id": str(uuid.uuid4()),
        "event_type": event_type,
        "occurred_at": datetime.now(timezone.utc).isoformat(),
        "account_id": account_id,
        "data": data,
    }
    try:
        eventbus.publish(topic=topic, key=account_id, payload=envelope)
    except EventBusError:
        _publish_failures.add(1, attributes={"topic": topic, "event_type": event_type})
        logger.warning("Failed to publish event %s to topic %s", event_type, topic, exc_info=True)


def publish_number_reserved(account_id: str, *, number_id: str, e164: str, country: str) -> None:
    publish_event(
        "zoiko.numbers", "number.reserved", account_id,
        {"number_id": number_id, "e164": e164, "country": country},
    )


def publish_number_activated(account_id: str, *, number_id: str, e164: str) -> None:
    publish_event("zoiko.numbers", "number.activated", account_id, {"number_id": number_id, "e164": e164})


def publish_number_suspended(account_id: str, *, number_id: str, e164: str, reason: str | None = None) -> None:
    publish_event(
        "zoiko.numbers", "number.suspended", account_id, {"number_id": number_id, "e164": e164, "reason": reason},
    )


def publish_call_started(
    account_id: str, *, call_sid: str, from_number: str, to_number: str, direction: str
) -> None:
    publish_event(
        "zoiko.calls", "call.started", account_id,
        {"call_sid": call_sid, "from_number": from_number, "to_number": to_number, "direction": direction},
    )


def publish_call_ended(account_id: str, *, call_sid: str, status: str, duration_seconds: int | None = None) -> None:
    publish_event(
        "zoiko.calls", "call.ended", account_id,
        {"call_sid": call_sid, "status": status, "duration_seconds": duration_seconds},
    )


def publish_notification_sent(account_id: str | None, *, event_name: str, channel: str, status: str) -> None:
    publish_event(
        "zoiko.notifications", "notification.sent", account_id,
        {"event_name": event_name, "channel": channel, "status": status},
    )


def publish_video_room_created(account_id: str, *, room_name: str) -> None:
    publish_event("zoiko.video", "video.room.created", account_id, {"room_name": room_name})


def publish_video_room_ended(account_id: str, *, room_name: str) -> None:
    publish_event("zoiko.video", "video.room.ended", account_id, {"room_name": room_name})


def publish_voicemail_created(account_id: str, *, voicemail_id: str, phone_number_id: str) -> None:
    publish_event(
        "zoiko.voicemail", "voicemail.created", account_id,
        {"voicemail_id": voicemail_id, "phone_number_id": phone_number_id},
    )


def publish_transcript_completed(
    account_id: str, *, summary_id: str, source_type: str, source_id: str, model_version: str
) -> None:
    publish_event(
        "zoiko.intelligence", "transcript.completed", account_id,
        {"summary_id": summary_id, "source_type": source_type, "source_id": source_id, "model_version": model_version},
    )


def publish_ai_summary_completed(
    account_id: str, *, summary_id: str, source_type: str, source_id: str, urgency: str | None
) -> None:
    publish_event(
        "zoiko.intelligence", "ai.summary.completed", account_id,
        {"summary_id": summary_id, "source_type": source_type, "source_id": source_id, "urgency": urgency},
    )


def publish_usage_rated(
    account_id: str, *, usage_event_id: str, event_type: str, quantity: float, unit: str, country_band: str | None
) -> None:
    publish_event(
        "zoiko.usage", "usage.rated", account_id,
        {
            "usage_event_id": usage_event_id, "event_type": event_type, "quantity": quantity,
            "unit": unit, "country_band": country_band,
        },
    )


def publish_compliance_case_required(
    account_id: str, *, case_id: str, jurisdiction: str, requirement_type: str
) -> None:
    publish_event(
        "zoiko.compliance", "compliance.case_required", account_id,
        {"case_id": case_id, "jurisdiction": jurisdiction, "requirement_type": requirement_type},
    )


def publish_compliance_case_approved(account_id: str, *, case_id: str, jurisdiction: str, requirement_type: str) -> None:
    publish_event(
        "zoiko.compliance", "compliance.case_approved", account_id,
        {"case_id": case_id, "jurisdiction": jurisdiction, "requirement_type": requirement_type},
    )


def publish_compliance_case_rejected(
    account_id: str, *, case_id: str, jurisdiction: str, requirement_type: str, reason: str | None
) -> None:
    publish_event(
        "zoiko.compliance", "compliance.case_rejected", account_id,
        {"case_id": case_id, "jurisdiction": jurisdiction, "requirement_type": requirement_type, "reason": reason},
    )
