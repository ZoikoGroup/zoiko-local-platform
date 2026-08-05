import enum
import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Enum, ForeignKey, Integer, String, Text, func
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
    # Roadmap "AI-driven fraud/spam signals" - platform-wide velocity signal,
    # not AI: set at record_call() time for INBOUND calls when from_number
    # has called several distinct accounts in a short window (see
    # app.risk.service.is_suspected_spam_caller). A real customer calls one
    # business; a robocall/spam campaign fans out across many.
    is_suspected_spam: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
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

    # Set once at creation (from the video-conferencing reference screenshot's
    # "Confidential Mode" toggle) - not changeable mid-call, since flipping it
    # on partway through would falsely imply everything said before the
    # switch was also protected. When true, recording is blocked outright
    # (see start_video_recording's ConfidentialModeRecordingBlockedError),
    # which transitively blocks AI summaries too - summarize_video_session
    # already requires a finished recording_url to exist, so a confidential
    # session that never has one can never be summarized either. No new
    # storage/consent bypass needed - just refusing to create the artifact
    # in the first place.
    confidential: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

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


class VideoWaitingGuestStatus(str, enum.Enum):
    PENDING = "pending"
    ADMITTED = "admitted"
    DENIED = "denied"


class VideoWaitingGuest(Base):
    """Host waiting-room / admission for guest video join (Roadmap-adjacent
    feature, built on top of the guest-join feature - see
    app.media.service.request_guest_join / admit_waiting_guest). A guest's
    join request lands here as PENDING instead of getting a LiveKit token
    immediately; the host admits or denies it, and only then does the
    guest's next poll receive a real token - never persisted here, since a
    LiveKit access token is itself a bearer credential and there's no
    reason to store one at rest when it's cheap to (re)generate on demand
    from guest_identity/display_name once admitted.

    No automatic expiry/cleanup of stale PENDING rows yet - a real product
    would want one (this session's scope stopped at proving the admission
    flow itself works), so this table will accumulate abandoned requests
    over time. Worth a retention-style purge job later, same pattern as
    app/retention/service.py, if this becomes a real problem.
    """

    __tablename__ = "video_waiting_guests"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=new_uuid)
    video_session_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("video_sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    display_name: Mapped[str] = mapped_column(String(100), nullable=False)
    # Reserved at request time (not at admission) so the SAME identity is
    # used throughout - the host is shown/approves this exact guest, not a
    # freshly-minted one that could theoretically differ.
    guest_identity: Mapped[str] = mapped_column(String(50), nullable=False, unique=True)
    status: Mapped[VideoWaitingGuestStatus] = mapped_column(
        Enum(VideoWaitingGuestStatus, name="video_waiting_guest_status_enum"),
        nullable=False,
        default=VideoWaitingGuestStatus.PENDING,
    )
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
    # Distinct from `escalated` above: escalation is an automatic, live-call
    # action (forwarding an urgent call to escalation_user_id while the
    # caller is still on the line). This is a post-hoc, human decision -
    # routing the already-captured summary card to a team member for
    # follow-up, set any time after the call ends via POST .../assign.
    assigned_user_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    # Output guardrail (Roadmap §7: "no pricing/legal/medical commitments") -
    # the system prompt tells the model not to do this, but this is what
    # actually checks whether it complied: categories the AI's OWN generated
    # summary/reason text was flagged for (see
    # app/intelligence/guardrails.py), empty when clean. Never derived from
    # raw_transcript - a caller mentioning a price or a lawsuit is normal.
    guardrail_flags: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    # AI governance: "human-editable outputs" - same pattern as
    # ConversationSummary.original_summary. Editing never re-runs the
    # guardrail check above: a human correcting/authorizing wording is a
    # deliberate decision, not unvetted model output.
    original_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    edited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    edited_by_user_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    model_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # AI content signal, distinct from CallRecord.is_suspected_spam's
    # velocity signal above - the LLM classifying the caller's OWN
    # transcript for scam/spam pitch patterns (fake prize, warranty scam,
    # generic sales pitch with no real reason for calling this business,
    # etc.), same qualify_caller() extraction pass as name/company/urgency.
    # Never set without AI-processing consent, same as every other field
    # qualify_caller() produces.
    is_likely_spam: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    spam_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
