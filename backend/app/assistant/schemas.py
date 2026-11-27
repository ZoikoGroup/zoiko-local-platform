"""Pydantic response/request models for the public assistant API
(WF-01's proposed surface, adapted to this codebase's /api/v1 careers
routing convention). Field names are snake_case, matching every other
module's JSON contract in this codebase.
"""

from pydantic import BaseModel, Field


class QuickStartAction(BaseModel):
    """WF-01 quick-start chip: a fixed, versioned intent the client can
    replay as intent_id on its next message."""

    intent_id: str
    title: str
    description: str


class BootstrapResponse(BaseModel):
    """WF-01 bootstrap payload: everything the widget needs to render
    before any session exists. `enabled` is the kill switch
    (settings.public_assistant_enabled) - false means the widget must
    not offer to open a chat at all."""

    enabled: bool
    assistant_name: str
    ai_disclosure: str
    safety_notice: str
    content_version: str
    supported_locales: list[str]
    default_locale: str
    privacy_url: str
    actions: list[QuickStartAction]


class CreateSessionRequest(BaseModel):
    page_context: str | None = Field(default=None, max_length=200)
    # WF-12: any locale is accepted but only reviewed locales resolve;
    # anything else fails closed to default_locale with locale_fallback
    # set on the session so the client knows the UI language it got.
    locale: str | None = Field(default=None, max_length=10)


class SessionCreatedResponse(BaseModel):
    """The opaque credentials the client keeps in memory (WF-10: no
    cookie, no account linkage). session_token is shown exactly once -
    only its SHA-256 hash is stored, so a lost response can never be
    recovered and the session must simply be re-created."""

    session_id: str
    session_token: str
    expires_at: str
    locale_used: str
    locale_fallback: bool


class SessionEndResponse(BaseModel):
    ended: bool
    session_id: str


class MessageSelection(BaseModel):
    """WF-02 guided-flow selections - the ONLY structured input the
    message endpoint accepts, deliberately typed as plain validated
    fields rather than free JSON so nothing unvalidated can enter
    flow_context."""

    country: str | None = Field(default=None, max_length=2)
    customer_type: str | None = Field(default=None, max_length=20)


class MessageRequest(BaseModel):
    """WF-01 message send. Exactly one of intent_id (quick-start replay),
    text (free-form), or selection (WF-02 flow step) drives the response;
    when several are present they are combined in a fixed order -
    intent_id first, then selection applied to the flow, then text -
    which mirrors the widget's own behavior (a chip click may carry the
    pending flow selection)."""

    intent_id: str | None = Field(default=None, max_length=50)
    text: str | None = Field(default=None, max_length=2000)
    selection: MessageSelection | None = None
    client_message_id: str | None = Field(default=None, max_length=64)


class Claim(BaseModel):
    """WF-11: an individual assertion plus the source slugs that back
    it. A claim with no citations is never emitted - the service drops
    it instead (the doc's core invariant)."""

    text: str
    source_slugs: list[str]


class SourceCard(BaseModel):
    """WF-11 evidence card, rendered by the widget under an answer."""

    slug: str
    title: str
    publisher: str
    canonical_url: str
    authority_class: str
    content_version: str
    reviewed_at: str | None = None


class AssistantAction(BaseModel):
    """WF-01/WF-03 CTA: resolved against the destination allowlist -
    the label and url always come from an AssistantDestination row, never
    from message text."""

    destination_key: str
    label: str
    url: str
    style: str = "secondary"


class Suggestion(BaseModel):
    """WF-02 quick-reply chips for guided flow steps (country picker,
    business/personal, example replay). `kind` tells the client how to
    render and what to send back."""

    label: str
    value: str
    kind: str


class AssistantMessageResponse(BaseModel):
    """WF-01/WF-11 response envelope. response_type is one of:
    guidance | answer | safety_urgent | sensitive_blocked | unknown.
    verifiability_status is WF-11's per-answer evidence verdict:
    'verified' (backed by an approved data source, e.g. the price
    catalog), 'qualified' (policy language with citations), or 'none'
    (safety scripts and unknown-fallback copy - deliberately uncited)."""

    message_id: str
    client_message_id: str | None = None
    response_type: str
    intent_id: str | None = None
    text: str
    claims: list[Claim]
    sources: list[SourceCard]
    actions: list[AssistantAction]
    suggestions: list[Suggestion]
    flow_step: str | None = None
    verifiability_status: str
    locale_used: str
    content_version: str
