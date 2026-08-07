from collections import defaultdict
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.intelligence.models import ConversationSummary
from app.media.models import CallRecord, VideoParticipantSession, VideoSession
from app.messaging.models import Conversation, Message
from app.numbering.numbers.models import PhoneNumber, PhoneNumberStatus

DEFAULT_RANGE_DAYS = 30
MAX_RANGE_DAYS = 90


def _clamp_days(days: int) -> int:
    return max(1, min(days, MAX_RANGE_DAYS))


def get_overview(db: Session, account_id: str, days: int = DEFAULT_RANGE_DAYS) -> dict:
    """Real aggregation over the account's own call/video/messaging history
    — no separate rollup table, since at this data volume querying the
    source tables directly on each request is simple and fast enough (same
    reasoning as usage/service.py's list_account_usage)."""
    days = _clamp_days(days)
    since = datetime.now(timezone.utc) - timedelta(days=days)
    now = datetime.now(timezone.utc)

    daily: dict = defaultdict(lambda: {"calls": 0, "call_minutes": 0.0, "video_minutes": 0.0, "messages": 0})

    calls = (
        db.query(CallRecord)
        .filter(CallRecord.account_id == account_id, CallRecord.created_at >= since)
        .all()
    )
    total_call_minutes = 0.0
    for call in calls:
        minutes = (call.duration or 0) / 60
        daily[call.created_at.date()]["calls"] += 1
        daily[call.created_at.date()]["call_minutes"] += minutes
        total_call_minutes += minutes

    participant_rows = (
        db.query(VideoParticipantSession)
        .join(VideoSession, VideoParticipantSession.video_session_id == VideoSession.id)
        .filter(VideoSession.account_id == account_id, VideoParticipantSession.joined_at >= since)
        .all()
    )
    total_video_minutes = 0.0
    for row in participant_rows:
        minutes = ((row.left_at or now) - row.joined_at).total_seconds() / 60
        daily[row.joined_at.date()]["video_minutes"] += minutes
        total_video_minutes += minutes

    messages = (
        db.query(Message)
        .join(Conversation, Message.conversation_id == Conversation.id)
        .filter(Conversation.account_id == account_id, Message.created_at >= since)
        .all()
    )
    for message in messages:
        daily[message.created_at.date()]["messages"] += 1

    active_numbers = (
        db.query(PhoneNumber)
        .filter(PhoneNumber.account_id == account_id, PhoneNumber.status == PhoneNumberStatus.ACTIVE)
        .count()
    )
    ai_summaries = (
        db.query(ConversationSummary)
        .filter(ConversationSummary.account_id == account_id, ConversationSummary.created_at >= since)
        .count()
    )

    daily_points = [
        {"date": d, **stats}
        for d, stats in sorted(daily.items())
    ]
    for point in daily_points:
        point["call_minutes"] = round(point["call_minutes"], 2)
        point["video_minutes"] = round(point["video_minutes"], 2)

    return {
        "range_days": days,
        "total_calls": len(calls),
        "total_call_minutes": round(total_call_minutes, 2),
        "total_video_minutes": round(total_video_minutes, 2),
        "total_messages": len(messages),
        "active_numbers": active_numbers,
        "ai_summaries": ai_summaries,
        "daily": daily_points,
    }
