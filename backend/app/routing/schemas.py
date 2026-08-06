from datetime import datetime, time

from pydantic import BaseModel, ConfigDict, Field


class CallFlowNode(BaseModel):
    """One step in a call flow's node graph. Only the fields relevant to
    `type` are meaningful - see routing/service.py's validate_flow() for
    what's required per type. Kept as one flexible model (all fields
    optional) rather than a discriminated union so the frontend can send
    a node mid-edit without every field filled in yet; validation only
    runs at publish time, not on every draft save.
    """

    id: str
    type: str  # "menu" | "business_hours" | "forward" | "voicemail" | "ai_receptionist" | "hangup"

    # menu
    prompt: str | None = None
    options: dict[str, str] | None = None  # digit -> next node id
    invalid_node_id: str | None = None  # default: repeat this same menu node
    timeout_node_id: str | None = None  # default: repeat this same menu node

    # business_hours
    start: time | None = None
    end: time | None = None
    timezone: str | None = None
    within_node_id: str | None = None
    outside_node_id: str | None = None

    # forward (destinations.length > 1 rings every destination simultaneously,
    # same ring-group semantics as numbers.models.RingGroupDestination)
    destinations: list[str] | None = None
    on_no_answer_node_id: str | None = None  # failover target; unset = voicemail

    # queue (contact-center-lite - a real FIFO hold queue, distinct from a
    # forward node's simultaneous ring group)
    queue_id: str | None = None
    overflow_node_id: str | None = None  # where a caller goes after max_wait_seconds; default: voicemail

    # hangup
    message: str | None = None


class CreateCallFlowRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class SaveDraftRequest(BaseModel):
    entry_node_id: str
    nodes: list[CallFlowNode]


class RollbackRequest(BaseModel):
    version: int


class AssignCallFlowRequest(BaseModel):
    phone_number_id: str | None  # null unassigns


class CallFlowVersionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    version: int
    status: str
    entry_node_id: str
    nodes: list[CallFlowNode]
    published_at: datetime | None
    rolled_back_from_version: int | None
    created_at: datetime


class CallFlowResponse(BaseModel):
    id: str
    account_id: str
    name: str
    created_at: datetime
    draft: CallFlowVersionResponse | None
    live: CallFlowVersionResponse | None
    version_history: list[CallFlowVersionResponse]


class CallFlowSummary(BaseModel):
    id: str
    name: str
    created_at: datetime
    has_draft: bool
    live_version: int | None
    assigned_numbers: list[str]


class PublishResult(BaseModel):
    published: bool
    errors: list[str] = []
    version: CallFlowVersionResponse | None = None
