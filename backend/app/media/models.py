import enum
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def new_uuid() -> str:
    return str(uuid.uuid4())


class CallDirection(str, enum.Enum):
    INBOUND = "inbound"
    OUTBOUND = "outbound"


class CallRecord(Base):
    __tablename__ = "call_records"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=new_uuid)
    # nullable: an inbound call to a number we don't recognize (not in phone_numbers,
    # e.g. mis-synced or foreign account) still gets logged, just without an owner
    account_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False), ForeignKey("accounts.id", ondelete="CASCADE"), nullable=True, index=True
    )
    phone_number_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False), ForeignKey("phone_numbers.id", ondelete="SET NULL"), nullable=True, index=True
    )
    direction: Mapped[CallDirection] = mapped_column(Enum(CallDirection, name="call_direction_enum"), nullable=False)
    from_number: Mapped[str] = mapped_column(String(20), nullable=False)
    to_number: Mapped[str] = mapped_column(String(20), nullable=False)
    provider_call_sid: Mapped[str | None] = mapped_column(String(50), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    duration: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Only ever set for a forwarded inbound call (the two-way conversation
    # path - see voice.py's should_forward_call branch) via Twilio's <Dial
    # record="record-from-answer-dual">. The other branches (voicemail,
    # receptionist, a bare unrecognized-number message) have their own
    # separate recording rows already, so recording the whole outer call
    # there would just duplicate audio that's already captured.
    recording_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class VideoSessionStatus(str, enum.Enum):
    CREATED = "created"
    ACTIVE = "active"
    ENDED = "ended"


class VideoSession(Base):
    __tablename__ = "video_sessions"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=new_uuid)
    account_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    host_user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    room_name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    status: Mapped[VideoSessionStatus] = mapped_column(
        Enum(VideoSessionStatus, name="video_session_status_enum"),
        nullable=False,
        default=VideoSessionStatus.CREATED,
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Roadmap "Recording: off by default... must be consented" - recording is
    # opt-in per session (never automatic like the voice-forwarding path),
    # gated on the same AI-processing consent record used for call/voicemail
    # summaries. egress_id correlates the LiveKit webhook's egress_ended
    # event back to this session once the file finishes processing.
    recording_egress_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    recording_url: Mapped[str | None] = mapped_column(String(500), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class VideoParticipantSession(Base):
    """Roadmap doc §8/§Usage Metering: 'video participant-minutes metered
    for future pricing' - one row per participant's time in a room, from
    LiveKit's participant_joined/left webhook events. Usage is the sum of
    (left_at or now) - joined_at across every row for a session, not just
    the room's own started_at/ended_at, since a session with 3 people for
    10 minutes each is 30 participant-minutes, not 10.
    """

    __tablename__ = "video_participant_sessions"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=new_uuid)
    video_session_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("video_sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    participant_identity: Mapped[str] = mapped_column(String(200), nullable=False)
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    left_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Voicemail(Base):
    __tablename__ = "voicemails"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=new_uuid)
    phone_number_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("phone_numbers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    account_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    from_number: Mapped[str] = mapped_column(String(20), nullable=False)
    recording_url: Mapped[str] = mapped_column(String(500), nullable=False)
    duration: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ReceptionistUrgency(str, enum.Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ReceptionistCall(Base):
    """Guarded caller-qualification capture (Roadmap §7 "AI Receptionist" —
    Phase 1 scope: message capture + urgency detection + routing, not a
    conversational agent). raw_transcript is Twilio's own speech-to-text
    from the Gather verb; the structured fields below are populated by an
    LLM extraction pass ONLY when AI-processing consent is on file — see
    intelligence.service.qualify_caller().
    """

    __tablename__ = "receptionist_calls"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=new_uuid)
    account_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    phone_number_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("phone_numbers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    call_sid: Mapped[str] = mapped_column(String(50), unique=True, nullable=False, index=True)
    caller_number: Mapped[str] = mapped_column(String(20), nullable=False)
    raw_transcript: Mapped[str] = mapped_column(Text, nullable=False)
    caller_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    caller_company: Mapped[str | None] = mapped_column(String(200), nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )  # full narrated sentence for display - reason stays a short fragment for internal use
    urgency: Mapped[ReceptionistUrgency | None] = mapped_column(
        Enum(ReceptionistUrgency, name="receptionist_urgency_enum"), nullable=True
    )
    escalated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    model_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
