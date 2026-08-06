from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.audit.service import log_event
from app.crm.models import CrmConnection, CrmProvider, CrmSyncEvent, CrmSyncEventType
from app.integrations.crm import mock as crm_adapter


class CrmAlreadyConnectedError(Exception):
    """Raised when trying to connect a CRM while one is already active -
    disconnect first, same posture as ZoikoNex only having one subscription
    per account."""


class CrmNotConnectedError(Exception):
    """Raised when trying to sync or disconnect with no active connection."""


def get_connection(db: Session, account_id: str) -> CrmConnection | None:
    return (
        db.query(CrmConnection)
        .filter(CrmConnection.account_id == account_id, CrmConnection.disconnected_at.is_(None))
        .first()
    )


def connect_crm(db: Session, *, account_id: str, provider: str, actor: str) -> CrmConnection:
    if get_connection(db, account_id) is not None:
        raise CrmAlreadyConnectedError("This account already has an active CRM connection - disconnect it first")

    provider_enum = CrmProvider(provider)
    result = crm_adapter.connect(account_id=account_id, provider=provider_enum.value)

    connection = CrmConnection(
        account_id=account_id, provider=provider_enum,
        external_ref=result["external_ref"], external_account_label=result["external_account_label"],
    )
    db.add(connection)
    db.commit()
    db.refresh(connection)

    log_event(
        db, actor=actor, action="crm_connection.connected", target=f"crm_connection:{connection.id}",
        after={"provider": provider_enum.value},
    )
    return connection


def disconnect_crm(db: Session, *, account_id: str, actor: str) -> None:
    connection = get_connection(db, account_id)
    if connection is None:
        raise CrmNotConnectedError("This account has no active CRM connection")

    connection.disconnected_at = datetime.now(timezone.utc)
    db.commit()

    log_event(db, actor=actor, action="crm_connection.disconnected", target=f"crm_connection:{connection.id}")


def sync_contact_to_crm(db: Session, *, account_id: str, contact_id: str, name: str, phone_number: str) -> None:
    """Best-effort, same posture as ZoikoNex's usage sync and webhook
    dispatch - a no-op when there's no active connection, and never
    allowed to fail the contact create/update that triggered it."""
    connection = get_connection(db, account_id)
    if connection is None:
        return

    result = crm_adapter.sync_contact(
        contact_id=contact_id, account_id=account_id, name=name, phone_number=phone_number
    )
    db.add(
        CrmSyncEvent(
            account_id=account_id, event_type=CrmSyncEventType.CONTACT_SYNC, external_ref=result["external_ref"],
            payload={"contact_id": contact_id, "name": name, "phone_number": phone_number},
        )
    )
    db.commit()


def sync_activity_to_crm(db: Session, *, account_id: str, event_type: str, contact_phone: str | None) -> None:
    """Wired into the same notifications.service.send_notification
    dispatch point webhooks use (see that function's docstring) - every
    notification-worthy event also becomes a CRM activity log entry when
    the account has an active connection."""
    connection = get_connection(db, account_id)
    if connection is None:
        return

    result = crm_adapter.sync_activity(account_id=account_id, event_type=event_type, contact_phone=contact_phone)
    db.add(
        CrmSyncEvent(
            account_id=account_id, event_type=CrmSyncEventType.ACTIVITY_SYNC, external_ref=result["external_ref"],
            payload={"event_type": event_type, "contact_phone": contact_phone},
        )
    )
    db.commit()


def list_sync_events(db: Session, account_id: str, limit: int = 100) -> list[CrmSyncEvent]:
    return (
        db.query(CrmSyncEvent)
        .filter(CrmSyncEvent.account_id == account_id)
        .order_by(CrmSyncEvent.created_at.desc())
        .limit(limit)
        .all()
    )
