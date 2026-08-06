import enum
import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, Enum, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def new_uuid() -> str:
    return str(uuid.uuid4())


class CallFlow(Base):
    """Architecture doc's "Call Flow Designer" (Phase 3 - Advanced IVR
    builder). This is the named container a business builds and assigns to
    one of its numbers - the actual routing logic lives versioned in
    CallFlowVersion below, never here, so publish/rollback never touches
    this row.
    """

    __tablename__ = "call_flows"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=new_uuid)
    account_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    created_by_user_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CallFlowVersionStatus(str, enum.Enum):
    DRAFT = "draft"
    PUBLISHED = "published"
    ARCHIVED = "archived"


class CallFlowVersion(Base):
    """One immutable snapshot of a CallFlow's node graph. Exactly one
    version per call_flow_id may be PUBLISHED (the "live" version an
    inbound call actually runs) at any time - enforced in
    routing.service.publish_version(), not a DB constraint, since the
    transition (archive old live, publish new) has to happen atomically
    inside one service call anyway.

    `nodes` is a JSON list of dicts, each shaped like:
        {"id": "n1", "type": "menu", "prompt": "...",
         "options": {"1": "n2", "2": "n3"},
         "invalid_node_id": "n1", "timeout_node_id": "n1"}
    Node types and their fields are documented in routing/schemas.py -
    kept as a flexible JSON blob (not a table per node type) because the
    architecture doc's Call Flow Designer needs the shape to evolve
    (queues, schedules) without a migration per node-type change.
    """

    __tablename__ = "call_flow_versions"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=new_uuid)
    call_flow_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("call_flows.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[CallFlowVersionStatus] = mapped_column(
        Enum(CallFlowVersionStatus, name="call_flow_version_status_enum"), nullable=False
    )
    entry_node_id: Mapped[str] = mapped_column(String(100), nullable=False)
    nodes: Mapped[list] = mapped_column(JSON, nullable=False, default=list)

    created_by_user_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    published_by_user_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    # Set only when this version was created by Call Flow Rollback (email
    # spec's ROUTE-003 "Call Flow Rollback") rather than a normal draft
    # edit - the audit trail should read "rolled back to version N", not
    # "published a new version" for these.
    rolled_back_from_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
