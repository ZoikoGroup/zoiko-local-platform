import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.ids import new_uuid


class CrmProvider(str, enum.Enum):
    HUBSPOT = "hubspot"
    SALESFORCE = "salesforce"
    PIPEDRIVE = "pipedrive"


class CrmSyncEventType(str, enum.Enum):
    CONTACT_SYNC = "contact_sync"
    ACTIVITY_SYNC = "activity_sync"


class CrmConnection(Base):
    """Architecture doc Phase 2 "CRM integrations" - explicitly a mock (see
    app.integrations.crm.mock's docstring), same disclosed-exception
    posture as the ZoikoNex billing mock. One row per account: a customer
    picks which of the three providers they're "connected" to purely for
    display/labeling purposes - the mock behaves identically regardless
    of which one is chosen, since there's no real client for any of them
    yet."""

    __tablename__ = "crm_connections"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=new_uuid)
    # Not unique - an account can disconnect and reconnect (to the same or
    # a different provider) over time. service.get_connection() finds the
    # active one via disconnected_at IS NULL; at most one row per account
    # should ever have that be true, enforced in code (connect_crm checks
    # get_connection() first), not the schema.
    account_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    provider: Mapped[CrmProvider] = mapped_column(Enum(CrmProvider, name="crm_provider_enum"), nullable=False)
    external_ref: Mapped[str] = mapped_column(String(100), nullable=False)
    external_account_label: Mapped[str] = mapped_column(String(255), nullable=False)
    connected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    disconnected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class CrmSyncEvent(Base):
    """Sync audit ledger - same role ZoikoNexSyncEvent plays for billing:
    a real, useful record even though the adapter behind it is mocked,
    since it catches genuine sync-wiring bugs and gives the customer
    (and eventually a real integration) something to reconcile against."""

    __tablename__ = "crm_sync_events"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=new_uuid)
    account_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    event_type: Mapped[CrmSyncEventType] = mapped_column(
        Enum(CrmSyncEventType, name="crm_sync_event_type_enum"), nullable=False
    )
    external_ref: Mapped[str] = mapped_column(String(100), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
