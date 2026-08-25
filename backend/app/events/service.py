import logging
import uuid
from datetime import datetime, timezone

from opentelemetry import metrics
from sqlalchemy.orm import Session

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


def publish_event_durably(db: Session, topic: str, event_type: str, account_id: str | None, data: dict) -> str:
    """The outbox half of publish_event - see EventOutbox's docstring for
    the full rationale. Call this BEFORE the caller's own db.commit()
    (only db.add()s here, deliberately never commits) so the event row
    lands in the exact same transaction as the business change it
    records - if that transaction rolls back, the event row never existed
    either. A separate sweep (flush_pending_outbox_events) actually
    publishes it to Kafka afterward, retrying until it succeeds.

    Returns the envelope's event_id (same shape publish_event's envelope
    uses) so a caller can log/reference it if useful."""
    from app.events.models import EventOutbox

    event_id = str(uuid.uuid4())
    envelope = {
        "event_id": event_id,
        "event_type": event_type,
        "occurred_at": datetime.now(timezone.utc).isoformat(),
        "account_id": account_id,
        "data": data,
    }
    db.add(EventOutbox(topic=topic, event_type=event_type, account_id=account_id, payload=envelope))
    return event_id


def flush_pending_outbox_events(db: Session, *, batch_size: int = 100) -> dict:
    """Actually publishes every not-yet-published EventOutbox row to
    Kafka, oldest first. Meant to run periodically (same manual-trigger-
    plus-external-cron pattern as app.compliance.service.sweep_expired_
    compliance_cases / app.billing.service.run_zoikonex_reconciliation),
    not on every request.

    Commits after EACH row (not once at the end) so one row's publish
    failure can't roll back the rows that already succeeded earlier in
    the same batch - matches the Kafka CONSUMER side's own per-message
    (not per-batch) commit granularity in app.events.consumer."""
    from app.events.models import EventOutbox

    pending = (
        db.query(EventOutbox)
        .filter(EventOutbox.published_at.is_(None))
        .order_by(EventOutbox.created_at.asc())
        .limit(batch_size)
        .all()
    )
    published, failed = 0, 0
    for row in pending:
        try:
            eventbus.publish(topic=row.topic, key=row.account_id, payload=row.payload)
        except EventBusError as e:
            row.attempt_count += 1
            row.last_error = str(e)[:500]
            failed += 1
        else:
            row.published_at = datetime.now(timezone.utc)
            published += 1
        db.commit()
    return {"checked": len(pending), "published": published, "failed": failed}


def publish_number_reserved(account_id: str, *, number_id: str, e164: str, country: str) -> None:
    publish_event(
        "zoiko.numbers", "number.reserved", account_id,
        {"number_id": number_id, "e164": e164, "country": country},
    )


def publish_number_purchase_confirmed(
    account_id: str, *, number_id: str, e164: str, payment_intent_id: str | None = None
) -> None:
    """There's no separate "order" entity in this codebase (unlike the
    Architecture doc's order.reference) - the Stripe payment_intent_id is
    the closest real correlation handle, when the purchase went through
    checkout rather than e.g. a staff override."""
    publish_event(
        "zoiko.numbers", "number.purchase_confirmed", account_id,
        {"number_id": number_id, "e164": e164, "payment_intent_id": payment_intent_id},
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


def publish_video_session_started(account_id: str, *, session_id: str, room_name: str) -> None:
    """Same fact as publish_video_room_created, under the Architecture doc's §8 event
    name. Kept as a separate publish alongside the room.* event rather than a rename,
    since something may already be keyed on video.room.created."""
    publish_event("zoiko.video", "video.session.started", account_id, {"session_id": session_id, "room_name": room_name})


def publish_video_session_ended(account_id: str, *, session_id: str, room_name: str) -> None:
    publish_event("zoiko.video", "video.session.ended", account_id, {"session_id": session_id, "room_name": room_name})


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


def publish_compliance_case_expired(
    account_id: str, *, case_id: str, jurisdiction: str, requirement_type: str
) -> None:
    """Architecture doc §8 event table - the one compliance event that had
    no real call site until app.compliance.service.expire_overdue_cases
    existed (a case was never actually marked EXPIRED anywhere in the
    codebase before that)."""
    publish_event(
        "zoiko.compliance", "compliance.case_expired", account_id,
        {"case_id": case_id, "jurisdiction": jurisdiction, "requirement_type": requirement_type},
    )


def publish_risk_signal_recorded(account_id: str, *, signal_type: str, detail: str) -> None:
    publish_event("zoiko.risk", "risk.signal_recorded", account_id, {"signal_type": signal_type, "detail": detail})


def publish_account_auto_suspended(account_id: str, *, score: int, numbers_suspended: list[str]) -> None:
    publish_event(
        "zoiko.risk", "risk.account_auto_suspended", account_id,
        {"score": score, "numbers_suspended": numbers_suspended},
    )


def publish_fraud_case_opened(account_id: str, *, case_id: str, score: int) -> None:
    publish_event("zoiko.risk", "risk.fraud_case_opened", account_id, {"case_id": case_id, "score": score})


def publish_fraud_case_resolved(account_id: str, *, case_id: str, status: str, notes: str | None) -> None:
    publish_event(
        "zoiko.risk", "risk.fraud_case_resolved", account_id, {"case_id": case_id, "status": status, "notes": notes},
    )


def publish_account_kill_switch_changed(account_id: str, *, scope: str, is_active: bool, actor: str) -> None:
    publish_event(
        "zoiko.risk", "risk.account_kill_switch_changed", account_id,
        {"scope": scope, "is_active": is_active, "actor": actor},
    )


def publish_subscription_plan_changed(
    account_id: str, *, subscription_id: str, previous_plan: str, new_plan: str
) -> None:
    publish_event(
        "zoiko.billing", "subscription.plan_changed", account_id,
        {"subscription_id": subscription_id, "previous_plan": previous_plan, "new_plan": new_plan},
    )


def publish_subscription_canceled(account_id: str, *, subscription_id: str, reason: str | None) -> None:
    publish_event(
        "zoiko.billing", "subscription.canceled", account_id,
        {"subscription_id": subscription_id, "reason": reason},
    )


def publish_subscription_payment_event(
    account_id: str, *, subscription_id: str, event_type: str, status: str
) -> None:
    publish_event(
        "zoiko.billing", "subscription.payment_event", account_id,
        {"subscription_id": subscription_id, "event_type": event_type, "status": status},
    )


def publish_payment_failed(account_id: str, *, subscription_id: str, reason: str | None) -> None:
    """Named event from the Architecture doc's §8 table. subscription.payment_event
    (below) already carries this same fact generically; this is additive, not a
    replacement, since something may already be keyed on the generic event."""
    publish_event(
        "zoiko.billing", "payment.failed", account_id,
        {"subscription_id": subscription_id, "reason": reason},
    )


def publish_payment_restored(account_id: str, *, subscription_id: str) -> None:
    publish_event("zoiko.billing", "payment.restored", account_id, {"subscription_id": subscription_id})


def publish_subscription_terminated(
    account_id: str, *, subscription_id: str, reason: str | None, numbers_released: int
) -> None:
    publish_event(
        "zoiko.billing", "subscription.terminated", account_id,
        {"subscription_id": subscription_id, "reason": reason, "numbers_released": numbers_released},
    )


def publish_porting_request_submitted(account_id: str, *, request_id: str, phone_number: str, country: str) -> None:
    publish_event(
        "zoiko.porting", "porting.request_submitted", account_id,
        {"request_id": request_id, "phone_number": phone_number, "country": country},
    )


def publish_porting_request_approved(account_id: str, *, request_id: str) -> None:
    publish_event("zoiko.porting", "porting.request_approved", account_id, {"request_id": request_id})


def publish_porting_request_rejected(account_id: str, *, request_id: str, reason: str | None) -> None:
    publish_event(
        "zoiko.porting", "porting.request_rejected", account_id, {"request_id": request_id, "reason": reason},
    )


def publish_porting_request_canceled(account_id: str, *, request_id: str) -> None:
    publish_event("zoiko.porting", "porting.request_canceled", account_id, {"request_id": request_id})


def publish_porting_request_completed(account_id: str, *, request_id: str, phone_number_id: str) -> None:
    publish_event(
        "zoiko.porting", "porting.request_completed", account_id,
        {"request_id": request_id, "phone_number_id": phone_number_id},
    )


def publish_message_sent(account_id: str, *, message_id: str, conversation_id: str, channel: str) -> None:
    publish_event(
        "zoiko.messaging", "message.sent", account_id,
        {"message_id": message_id, "conversation_id": conversation_id, "channel": channel},
    )


def publish_message_received(account_id: str, *, message_id: str, conversation_id: str, channel: str) -> None:
    publish_event(
        "zoiko.messaging", "message.received", account_id,
        {"message_id": message_id, "conversation_id": conversation_id, "channel": channel},
    )


def publish_queue_created(account_id: str, *, queue_id: str, name: str) -> None:
    publish_event("zoiko.queues", "queue.created", account_id, {"queue_id": queue_id, "name": name})


def publish_agent_presence_changed(account_id: str, *, user_id: str, status: str) -> None:
    publish_event(
        "zoiko.queues", "queue.agent_presence_changed", account_id, {"user_id": user_id, "status": status},
    )


def publish_crm_connected(account_id: str, *, provider: str) -> None:
    publish_event("zoiko.crm", "crm.connected", account_id, {"provider": provider})


def publish_crm_disconnected(account_id: str, *, provider: str) -> None:
    publish_event("zoiko.crm", "crm.disconnected", account_id, {"provider": provider})


def publish_api_key_created(account_id: str, *, key_id: str, label: str) -> None:
    publish_event("zoiko.apikeys", "api_key.created", account_id, {"key_id": key_id, "label": label})


def publish_api_key_revoked(account_id: str, *, key_id: str) -> None:
    publish_event("zoiko.apikeys", "api_key.revoked", account_id, {"key_id": key_id})


def publish_account_billing_classification_updated(
    account_id: str, *, billing_classification: str, billing_source: str
) -> None:
    publish_event(
        "zoiko.staff", "account.billing_classification_updated", account_id,
        {"billing_classification": billing_classification, "billing_source": billing_source},
    )


def publish_consent_granted(account_id: str, *, consent_type: str, jurisdiction: str) -> None:
    publish_event(
        "zoiko.consent", "consent.granted", account_id,
        {"consent_type": consent_type, "jurisdiction": jurisdiction},
    )


def publish_consent_revoked(account_id: str, *, consent_type: str, jurisdiction: str) -> None:
    publish_event(
        "zoiko.consent", "consent.revoked", account_id,
        {"consent_type": consent_type, "jurisdiction": jurisdiction},
    )


def publish_contact_created(account_id: str, *, contact_id: str, name: str) -> None:
    publish_event("zoiko.contacts", "contact.created", account_id, {"contact_id": contact_id, "name": name})


def publish_contact_updated(account_id: str, *, contact_id: str) -> None:
    publish_event("zoiko.contacts", "contact.updated", account_id, {"contact_id": contact_id})


def publish_contact_deleted(account_id: str, *, contact_id: str) -> None:
    publish_event("zoiko.contacts", "contact.deleted", account_id, {"contact_id": contact_id})


def publish_kill_switch_changed(*, scope: str, is_active: bool, actor: str) -> None:
    publish_event(
        "zoiko.ops", "ops.kill_switch_changed", None, {"scope": scope, "is_active": is_active, "actor": actor},
    )


def publish_incident_declared(*, incident_id: str, title: str, affected_service: str) -> None:
    publish_event(
        "zoiko.ops", "ops.incident_declared", None,
        {"incident_id": incident_id, "title": title, "affected_service": affected_service},
    )


def publish_incident_resolved(*, incident_id: str) -> None:
    publish_event("zoiko.ops", "ops.incident_resolved", None, {"incident_id": incident_id})


def publish_retention_policy_set(account_id: str, *, artifact_type: str, retention_days: int) -> None:
    publish_event(
        "zoiko.retention", "retention.policy_set", account_id,
        {"artifact_type": artifact_type, "retention_days": retention_days},
    )


def publish_retention_erasure_requested(account_id: str, *, request_id: str) -> None:
    publish_event("zoiko.retention", "retention.erasure_requested", account_id, {"request_id": request_id})


def publish_retention_recording_purged(account_id: str | None, *, artifact_type: str, target_id: str) -> None:
    publish_event(
        "zoiko.retention", "retention.recording_purged", account_id,
        {"artifact_type": artifact_type, "target_id": target_id},
    )


def publish_call_flow_published(account_id: str, *, call_flow_id: str, version: int) -> None:
    publish_event(
        "zoiko.routing", "call_flow.published", account_id,
        {"call_flow_id": call_flow_id, "version": version},
    )


def publish_call_flow_rolled_back(account_id: str, *, call_flow_id: str, restored_version: int, new_version: int) -> None:
    publish_event(
        "zoiko.routing", "call_flow.rolled_back", account_id,
        {"call_flow_id": call_flow_id, "restored_version": restored_version, "new_version": new_version},
    )


def publish_audit_event_recorded(account_id: str | None, *, audit_id: str, action: str, target: str) -> None:
    publish_event("zoiko.audit", "audit.event.recorded", account_id, {"audit_id": audit_id, "action": action, "target": target})
