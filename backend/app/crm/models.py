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
    # Every table needs a UUID primary key + created_at (project convention)
    # - this table only had connected_at (semantically "when this specific
    # connection/provider was linked", which is overwritten in spirit each
    # time the account reconnects, possibly to a different provider), never
    # a true immutable row-creation timestamp. Same server_default=func.now()
    # style as connected_at and every other created_at column in this
    # codebase.
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    connected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    disconnected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Real OAuth token storage - only ever populated for a real integration
    # (currently: HubSpot via app.integrations.crm.hubspot). NULL for mock
    # connections (Salesforce, Pipedrive, or HubSpot before real credentials
    # exist), which is how app.crm.service tells a real connection from a
    # mock one. Encrypted at rest via app.core.crypto - never store OAuth
    # tokens as plaintext. access_token_encrypted is refreshed in place
    # using refresh_token_encrypted once token_expires_at passes.
    access_token_encrypted: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    refresh_token_encrypted: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    # HubSpot's tokens have a fixed, told-to-you lifetime (see
    # token_expires_at); Salesforce's don't, so Salesforce leaves this NULL
    # and instead refreshes reactively on a 401 - see
    # app.crm.service._call_salesforce_with_reauth.
    token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Salesforce-specific: every API call must target the customer's own
    # org domain (e.g. https://mycompany.my.salesforce.com), returned once
    # at OAuth time - there's no single fixed API host the way HubSpot has
    # api.hubapi.com. NULL for every other provider.
    instance_url: Mapped[str | None] = mapped_column(String(255), nullable=True)


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
