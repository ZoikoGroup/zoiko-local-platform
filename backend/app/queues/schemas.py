from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class CreateQueueRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    max_wait_seconds: int = Field(default=120, ge=10, le=1800)
    wrap_up_seconds: int = Field(default=30, ge=0, le=600)


class UpdateQueueRequest(BaseModel):
    name: str | None = None
    max_wait_seconds: int | None = Field(default=None, ge=10, le=1800)
    wrap_up_seconds: int | None = Field(default=None, ge=0, le=600)


class AddMemberRequest(BaseModel):
    user_id: str


class SetPresenceRequest(BaseModel):
    status: str  # "available" | "offline" - "wrap_up" is system-managed only


class MemberResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    user_id: str
    email: str


class QueueResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    account_id: str
    name: str
    max_wait_seconds: int
    wrap_up_seconds: int
    created_at: datetime
    members: list[MemberResponse] = []


class QueueStatusResponse(BaseModel):
    queue_id: str
    waiting_count: int
    in_progress_count: int
    longest_wait_seconds: int
    sla_breached: bool


class PresenceResponse(BaseModel):
    status: str
    changed_at: datetime
    wrap_up_until: datetime | None
    effectively_available: bool


class PullNextResult(BaseModel):
    call_sid: str
    caller_number: str
    queue_call_log_id: str
