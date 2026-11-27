from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.analytics.service import get_overview
from app.billing import service as billing_service
from app.consent.service import get_consent_status
from app.media import service as media_service
from app.messaging.service import list_conversations
from app.numbering.identity.models import User
from app.numbering.numbers.service import list_account_numbers, list_supported_countries
from app.ops import service as ops_service
from app.platform_assistant import content
from app.porting.service import list_my_porting_requests
from app.retention.service import is_account_under_legal_hold, list_retention_policies


@dataclass
class ToolResult:
    answer_type: str
    content_blocks: list[dict] = field(default_factory=list)
    evidence: list[dict] = field(default_factory=list)
    actions: list[dict] = field(default_factory=list)
    uncertainty: str = "low"
    risk_tier: str = "R1"
    status: str = "ok"


def run_tool(tool_key: str, db: Session, user: User) -> ToolResult:
    handlers = {
        "overview": _overview,
        "calls": _calls,
        "receptionist": _receptionist,
        "numbers": _numbers,
        "porting": _porting,
        "billing": _billing,
        "inbox": _inbox,
        "analytics": _analytics,
        "acquisition": _acquisition,
        "video": _video,
        "compliance": _compliance,
        "support": _support,
    }
    handler = handlers.get(tool_key)
    if handler is None:
        return _fallback()
    try:
        return handler(db, user)
    except Exception:
        return _fallback()


def _fallback() -> ToolResult:
    return ToolResult(
        answer_type="unknown",
        content_blocks=[_paragraph(content.UNKNOWN_FALLBACK)],
        evidence=[],
        actions=[_action("DASHBOARD_HOME")],
        uncertainty="high",
        risk_tier="R0",
        status="unavailable",
    )


def _denied(destination_key: str, reason: str | None = None) -> ToolResult:
    blocks = [_paragraph(content.PERMISSION_FALLBACK)]
    actions = [_action(destination_key)]
    if reason:
        blocks.append(_paragraph(reason))
        blocks.append(_paragraph("Open Billing to review what your plan includes."))
        actions.append(_action("DASHBOARD_BILLING"))
    return ToolResult(
        answer_type="permission_denied",
        content_blocks=blocks,
        evidence=[],
        actions=actions,
        uncertainty="low",
        risk_tier="R1",
        status="denied",
    )


def _paragraph(text: str) -> dict:
    return {"type": "paragraph", "text": text, "items": []}


def _bullets(items: list[str]) -> dict:
    return {"type": "bullets", "text": None, "items": items[:8]}


def _evidence(source_id: str) -> dict:
    return {
        "source_id": source_id,
        "title": content.EVIDENCE_SOURCES.get(source_id, source_id),
        "retrieved_at": _iso(datetime.now(timezone.utc)),
        "masked": True,
    }


def _action(destination_key: str) -> dict:
    destination = content.DESTINATIONS.get(destination_key, content.DESTINATIONS["DASHBOARD_HOME"])
    return {
        "kind": "navigate",
        "destination_key": destination_key,
        "label": destination["label"],
        "href": destination["href"],
        "read_only": True,
    }


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _attr(obj: Any, *names: str) -> Any:
    for name in names:
        value = getattr(obj, name, None)
        if value is not None:
            return value
    return None


def _scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "yes" if value else "no"
    return str(getattr(value, "value", value))


def _mask(value: Any) -> str:
    text = _scalar(value)
    digits = "".join(character for character in text if character.isdigit())
    if len(digits) >= 4:
        return f"••••{digits[-4:]}"
    return "••••"


def _item_summary(row: Any, fields: list[tuple[str, list[str]]], mask_fields: set[str] | None = None) -> str:
    mask_fields = mask_fields or set()
    parts = []
    for label, names in fields:
        value = _attr(row, *names)
        if value is None:
            continue
        rendered = _mask(value) if label in mask_fields else _scalar(value)
        parts.append(f"{label}: {rendered}")
    return " | ".join(parts) if parts else "Record available"


def _overview(db: Session, user: User) -> ToolResult:
    numbers = list_account_numbers(db, user.account_id, user=user)
    calls = media_service.list_account_calls(db, user, limit=5)
    return ToolResult(
        answer_type="answer",
        content_blocks=[
            _paragraph("Here is the read-only view of your workspace."),
            _bullets([
                f"Numbers available to you: {len(numbers)}",
                f"Recent calls available to you: {len(calls)}",
                content.SUMMARY_CAVEAT,
            ]),
        ],
        evidence=[_evidence("media.numbers"), _evidence("media.calls")],
        actions=[_action("DASHBOARD_HOME")],
    )


def _calls(db: Session, user: User) -> ToolResult:
    calls = media_service.list_account_calls(db, user, limit=5)
    summaries = [
        _item_summary(
            call,
            [
                ("status", ["status"]),
                ("direction", ["direction"]),
                ("started", ["started_at", "created_at"]),
                ("duration", ["duration_seconds"]),
            ],
        )
        for call in calls
    ]
    blocks = [_paragraph("Recent calls available to you:"), _bullets(summaries or ["No recent calls found."]), _paragraph(content.SUMMARY_CAVEAT)]
    return ToolResult(answer_type="answer", content_blocks=blocks, evidence=[_evidence("media.calls")], actions=[_action("DASHBOARD_CALLS")])


def _receptionist(db: Session, user: User) -> ToolResult:
    if not billing_service.is_ai_receptionist_enabled_for_account(db, user.account_id):
        return _denied("DASHBOARD_CALL_FLOWS", "The AI Receptionist isn't enabled on your current plan.")
    calls = media_service.list_account_receptionist_calls(db, user)
    summaries = [
        _item_summary(call, [("status", ["status"]), ("urgency", ["urgency"]), ("created", ["created_at"])])
        for call in calls
    ]
    blocks = [_paragraph("AI Receptionist call history available to you:"), _bullets(summaries or ["No Receptionist calls found."]), _paragraph(content.SUMMARY_CAVEAT)]
    return ToolResult(answer_type="answer", content_blocks=blocks, evidence=[_evidence("media.receptionist")], actions=[_action("DASHBOARD_CALL_FLOWS")])


def _numbers(db: Session, user: User) -> ToolResult:
    numbers = list_account_numbers(db, user.account_id, user=user)
    summaries = [
        _item_summary(number, [("number", ["e164", "phone_number"]), ("status", ["status"]), ("country", ["country_code"])], {"number"})
        for number in numbers
    ]
    blocks = [_paragraph("Numbers in this workspace that you can view:"), _bullets(summaries or ["No numbers found."])]
    return ToolResult(answer_type="answer", content_blocks=blocks, evidence=[_evidence("media.numbers")], actions=[_action("DASHBOARD_NUMBERS")])


def _porting(db: Session, user: User) -> ToolResult:
    requests = list_my_porting_requests(db, user.account_id)
    summaries = [
        _item_summary(item, [("status", ["status"]), ("country", ["country_code"]), ("created", ["created_at"])])
        for item in requests
    ]
    blocks = [_paragraph("Porting requests for this workspace:"), _bullets(summaries or ["No porting requests found."])]
    return ToolResult(answer_type="answer", content_blocks=blocks, evidence=[_evidence("porting.requests")], actions=[_action("DASHBOARD_NUMBERS")])


def _billing(db: Session, user: User) -> ToolResult:
    snapshot = billing_service.get_entitlement_snapshot(db, user.account_id)
    enabled = [key for key, value in snapshot.items() if value is True]
    usage = billing_service.get_usage_summary(db, user.account_id)
    resources = usage.get("resources", []) if isinstance(usage, dict) else []
    usage_lines = [
        f"{item.get('resource', 'usage')}: {item.get('used', 0)} of {item.get('limit', 'unlimited')}"
        for item in resources
        if isinstance(item, dict) and item.get("used", 0)
    ]
    plan_name = usage.get("plan_name") if isinstance(usage, dict) else None
    history = billing_service.list_account_billing_history(db, user.account_id, limit=3)
    lines = [f"Enabled entitlements: {', '.join(enabled[:8]) if enabled else 'none'}."]
    if plan_name:
        lines.append(f"Plan: {plan_name}.")
    lines.append(f"Metered usage: {', '.join(usage_lines[:6]) if usage_lines else 'none'}.")
    lines.append(f"Recent billing records: {len(history)}.")
    blocks = [
        _paragraph("Billing information available to this workspace:"),
        _bullets(lines),
        _paragraph("Amounts and invoices remain in the secure billing screen."),
    ]
    evidence = [_evidence("billing.entitlements"), _evidence("billing.usage"), _evidence("billing.history")]
    return ToolResult(answer_type="answer", content_blocks=blocks, evidence=evidence, actions=[_action("DASHBOARD_BILLING")])


def _inbox(db: Session, user: User) -> ToolResult:
    if not billing_service.has_entitlement(db, user.account_id, "messaging.enabled"):
        return _denied("DASHBOARD_MESSAGING", "Messaging and voicemail aren't enabled on your current plan.")
    conversations = list_conversations(db, user.account_id)
    voicemails = media_service.list_account_voicemails(db, user)
    blocks = [
        _paragraph("Inbox information available to you:"),
        _bullets([
            f"Conversations: {len(conversations)}",
            f"Voicemails: {len(voicemails)}",
            "Message content is not displayed here.",
        ]),
    ]
    evidence = [_evidence("messaging.conversations"), _evidence("media.voicemail")]
    actions = [_action("DASHBOARD_MESSAGING"), _action("DASHBOARD_VOICEMAIL")]
    return ToolResult(answer_type="answer", content_blocks=blocks, evidence=evidence, actions=actions)


def _analytics(db: Session, user: User) -> ToolResult:
    if not billing_service.has_entitlement(db, user.account_id, "reporting.advanced"):
        return _denied("DASHBOARD_REPORTS", "Advanced reporting isn't enabled on your current plan.")
    overview = get_overview(db, user.account_id)
    metrics = [f"{key}: {value}" for key, value in overview.items() if isinstance(value, (int, float))]
    blocks = [_paragraph("Operational overview available to this workspace:"), _bullets(metrics or ["No summary metrics available."]), _paragraph(content.SUMMARY_CAVEAT)]
    return ToolResult(answer_type="answer", content_blocks=blocks, evidence=[_evidence("analytics.overview")], actions=[_action("DASHBOARD_REPORTS")])


def _acquisition(db: Session, user: User) -> ToolResult:
    countries = list_supported_countries(db)
    names = [_scalar(_attr(country, "name", "country_name", "code")) for country in countries[:8]]
    blocks = [_paragraph("Supported countries from the active registry:"), _bullets(names or ["No countries found."]), _paragraph("Availability is confirmed only in the secure acquisition flow.")]
    return ToolResult(answer_type="answer", content_blocks=blocks, evidence=[_evidence("numbering.countries")], actions=[_action("DASHBOARD_NUMBERS")])


def _video(db: Session, user: User) -> ToolResult:
    sessions = media_service.list_account_video_sessions(db, user.account_id)
    summaries = [
        _item_summary(session, [("status", ["status"]), ("created", ["created_at", "started_at"])])
        for session in sessions
    ]
    blocks = [_paragraph("Video sessions in this workspace:"), _bullets(summaries or ["No video sessions found."])]
    return ToolResult(answer_type="answer", content_blocks=blocks, evidence=[_evidence("media.video")], actions=[_action("DASHBOARD_VIDEO")])


def _compliance(db: Session, user: User) -> ToolResult:
    consent = get_consent_status(db, user.account_id)
    policies = list_retention_policies(db, user.account_id)
    legal_hold = is_account_under_legal_hold(db, user.account_id)
    blocks = [
        _paragraph("Compliance posture available to you:"),
        _bullets([
            f"Consent records: {len(consent)}",
            f"Retention policy categories: {len(policies)}",
            f"Legal hold: {'yes' if legal_hold else 'no'}",
            "This is not legal advice.",
        ]),
    ]
    evidence = [_evidence("consent.status"), _evidence("retention.policies")]
    return ToolResult(answer_type="answer", content_blocks=blocks, evidence=evidence, actions=[_action("DASHBOARD_COMPLIANCE")])


def _support(db: Session, user: User) -> ToolResult:
    incidents = ops_service.list_incidents(db, limit=5)
    synthetic = ops_service.get_synthetic_check_summary(db)
    incident_lines = [_item_summary(item, [("status", ["status"]),("service", ["affected_service"]), ("updated", ["updated_at"])]) for item in incidents]
    healthy = synthetic.get("overall_healthy") if isinstance(synthetic, dict) else None
    overall = "healthy" if healthy is True else "attention needed" if healthy is False else "unavailable"
    blocks = [
        _paragraph("Current support and status information:"),
        _bullets([f"Overall check status: {overall}", *incident_lines] or ["No incidents found."]),
        _paragraph("For help with a specific account issue, use the secure support screen."),
    ]
    evidence = [_evidence("ops.incidents"), _evidence("ops.synthetic")]
    return ToolResult(answer_type="answer", content_blocks=blocks, evidence=evidence, actions=[_action("DASHBOARD_SUPPORT")])
