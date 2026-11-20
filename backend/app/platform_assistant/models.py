from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.ids import new_uuid


class PlatformAssistantSession(Base):
    __tablename__ = "platform_assistant_sessions"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=new_uuid)
    user_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False, index=True)
    account_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False, index=True)
    session_token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    module: Mapped[str] = mapped_column(String(40), nullable=False, default="overview")
    context_key: Mapped[str | None] = mapped_column(String(80), nullable=True)
    context_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    message_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    invalidation_reason: Mapped[str | None] = mapped_column(String(60), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PlatformAssistantContent(Base):
    __tablename__ = "platform_assistant_content"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=new_uuid)
    content_key: Mapped[str] = mapped_column(String(80), nullable=False, unique=True, index=True)
    title: Mapped[str] = mapped_column(String(120), nullable=False)
    category: Mapped[str] = mapped_column(String(40), nullable=False, default="copy")
    body: Mapped[str] = mapped_column(Text, nullable=False)
    content_version: Mapped[str] = mapped_column(String(30), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class PlatformAssistantEvent(Base):
    __tablename__ = "platform_assistant_events"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=new_uuid)
    session_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False), nullable=True, index=True)
    user_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False, index=True)
    account_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(60), nullable=False, index=True)
    module: Mapped[str] = mapped_column(String(40), nullable=False, default="overview")
    risk_tier: Mapped[str] = mapped_column(String(10), nullable=False, default="R0")
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="ok")
    destination_key: Mapped[str | None] = mapped_column(String(60), nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    metadata_json: Mapped[dict] = mapped_column("metadata", JSONB, nullable=False, default=dict, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
