from typing import Literal

from pydantic import BaseModel, Field


class ModuleInfo(BaseModel):
    key: str
    title: str
    description: str
    prompts: list[str]
    destination_key: str


class BootstrapResponse(BaseModel):
    enabled: bool
    assistant_name: str
    ai_disclosure: str
    read_only_notice: str
    content_version: str
    supported_modules: list[ModuleInfo]


class CreateSessionRequest(BaseModel):
    module: str = Field(default="overview", max_length=40)
    context_key: str | None = Field(default=None, max_length=80)


class SessionContext(BaseModel):
    module: str
    role: str
    tenant_scope: str = "server-derived"
    workspace_label: str = "Current workspace"
    context_key: str | None = None
    expires_at: str


class SessionCreatedResponse(BaseModel):
    session_id: str
    session_token: str
    expires_at: str
    context: SessionContext


class SessionResponse(BaseModel):
    session_id: str
    context: SessionContext
    message_count: int
    ended: bool
    transcript_stored: bool = False


class SessionEndResponse(BaseModel):
    ended: bool
    session_id: str


class MessageRequest(BaseModel):
    text: str = Field(min_length=1, max_length=1000)
    intent: str | None = Field(default=None, max_length=60)
    client_message_id: str | None = Field(default=None, max_length=64)


class ContentBlock(BaseModel):
    type: str
    text: str | None = None
    items: list[str] = Field(default_factory=list)


class EvidenceItem(BaseModel):
    source_id: str
    title: str
    retrieved_at: str | None = None
    masked: bool = True


class NavigationAction(BaseModel):
    kind: str = "navigate"
    destination_key: str
    label: str
    href: str
    read_only: bool = True


class AssistantMessageResponse(BaseModel):
    message_id: str
    client_message_id: str | None = None
    request_id: str
    answer_type: str
    tenant_scope: str = "server-derived"
    context: SessionContext
    content_blocks: list[ContentBlock]
    evidence: list[EvidenceItem]
    available_actions: list[NavigationAction]
    uncertainty: str
    risk_tier: str
    read_only: bool = True
    content_version: str


class FeedbackRequest(BaseModel):
    message_id: str = Field(max_length=64)
    rating: Literal["helpful", "not_helpful"]
    category: str | None = Field(default=None, max_length=40)
