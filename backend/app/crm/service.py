import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.audit.service import log_event
from app.core.crypto import decrypt_secret, encrypt_secret
from app.core.security import create_access_token, decode_access_token
from app.crm.models import CrmConnection, CrmProvider, CrmSyncEvent, CrmSyncEventType
from app.events.service import publish_crm_connected, publish_crm_disconnected
from app.integrations.crm import hubspot as hubspot_adapter
from app.integrations.crm import mock as crm_adapter
from app.integrations.crm import pipedrive as pipedrive_adapter
from app.integrations.crm import salesforce as salesforce_adapter
from app.notifications.service import notify_integration_installed, notify_integration_removed

_HUBSPOT_OAUTH_STATE_SCOPE = "crm_hubspot_oauth_state"
_SALESFORCE_OAUTH_STATE_SCOPE = "crm_salesforce_oauth_state"
_PIPEDRIVE_OAUTH_STATE_SCOPE = "crm_pipedrive_oauth_state"
logger = logging.getLogger("zoiko.crm")


class CrmAlreadyConnectedError(Exception):
    """Raised when trying to connect a CRM while one is already active -
    disconnect first, same posture as ZoikoNex only having one subscription
    per account."""


class CrmNotConnectedError(Exception):
    """Raised when trying to sync or disconnect with no active connection."""


class HubSpotOAuthStateError(Exception):
    """Raised when the OAuth callback's state param is missing, expired, or
    doesn't decode. The callback route is unauthenticated (HubSpot's
    redirect carries no bearer token) - this signed, short-lived state is
    what stands in for a session, protecting against CSRF and replay."""


class SalesforceOAuthStateError(Exception):
    """Same rationale as HubSpotOAuthStateError, for Salesforce's callback."""


class PipedriveOAuthStateError(Exception):
    """Same rationale as HubSpotOAuthStateError, for Pipedrive's callback."""


def _is_real_hubspot_connection(connection: CrmConnection) -> bool:
    return connection.provider == CrmProvider.HUBSPOT and connection.access_token_encrypted is not None


def _is_real_salesforce_connection(connection: CrmConnection) -> bool:
    return connection.provider == CrmProvider.SALESFORCE and connection.access_token_encrypted is not None


def _is_real_pipedrive_connection(connection: CrmConnection) -> bool:
    return connection.provider == CrmProvider.PIPEDRIVE and connection.access_token_encrypted is not None


def get_connection(db: Session, account_id: str) -> CrmConnection | None:
    return (
        db.query(CrmConnection)
        .filter(CrmConnection.account_id == account_id, CrmConnection.disconnected_at.is_(None))
        .first()
    )


def connect_crm(db: Session, *, account_id: str, provider: str, actor: str) -> CrmConnection:
    """Historical mock-only connect path - kept for backward API
    compatibility, but every CrmProvider value now has a real OAuth
    integration (see build_*_authorize_url/complete_*_oauth below) and is
    deliberately rejected here, so this always 422s. A customer can never
    end up with a fake connection that looks identical to a real one -
    see app.integrations.crm.mock's docstring."""
    if provider == CrmProvider.HUBSPOT.value:
        raise ValueError("HubSpot uses a real OAuth connection - use GET /crm/hubspot/authorize instead")
    if provider == CrmProvider.SALESFORCE.value:
        raise ValueError("Salesforce uses a real OAuth connection - use GET /crm/salesforce/authorize instead")
    if provider == CrmProvider.PIPEDRIVE.value:
        raise ValueError("Pipedrive uses a real OAuth connection - use GET /crm/pipedrive/authorize instead")

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
    publish_crm_connected(account_id, provider=provider_enum.value)

    from app.numbering.identity.models import Account, User, UserRole
    actor_user = db.query(User).filter(User.id == actor).first()
    owner = db.query(User).filter(User.account_id == account_id, User.role == UserRole.OWNER).first()
    if owner is not None:
        account = db.query(Account).filter(Account.id == account_id).first()
        notify_integration_installed(
            db, account_id=account_id, account_email=owner.email,
            integration_name=f"{provider_enum.value.title()} CRM",
            organization_name=account.name if account else "your organization",
            actor_display_name=actor_user.email if actor_user else "your account",
        )

    return connection


def disconnect_crm(db: Session, *, account_id: str, actor: str) -> None:
    connection = get_connection(db, account_id)
    if connection is None:
        raise CrmNotConnectedError("This account has no active CRM connection")

    connection.disconnected_at = datetime.now(timezone.utc)
    db.commit()

    log_event(db, actor=actor, action="crm_connection.disconnected", target=f"crm_connection:{connection.id}")
    publish_crm_disconnected(account_id, provider=connection.provider.value)

    from app.numbering.identity.models import Account, User, UserRole

    owner = db.query(User).filter(User.account_id == account_id, User.role == UserRole.OWNER).first()
    if owner is not None:
        account = db.query(Account).filter(Account.id == account_id).first()
        notify_integration_removed(
            db, account_id=account_id, account_email=owner.email,
            integration_name=f"{connection.provider.value.title()} CRM",
            organization_name=account.name if account else "your organization",
        )


def build_hubspot_authorize_url(account_id: str) -> str:
    """A short-lived (15 minute), scoped JWT carries the account_id through
    HubSpot's redirect - reusing app.core.security's existing signed-token
    infra rather than hand-rolling a second signing scheme."""
    state = create_access_token(subject=account_id, scope=_HUBSPOT_OAUTH_STATE_SCOPE, expire_minutes=15)
    return hubspot_adapter.build_authorize_url(state=state)


def complete_hubspot_oauth(db: Session, *, code: str, state: str) -> CrmConnection:
    payload = decode_access_token(state)
    if payload is None or payload.get("scope") != _HUBSPOT_OAUTH_STATE_SCOPE:
        raise HubSpotOAuthStateError("Invalid or expired HubSpot OAuth state")
    account_id = payload["sub"]

    if get_connection(db, account_id) is not None:
        raise CrmAlreadyConnectedError("This account already has an active CRM connection - disconnect it first")

    tokens = hubspot_adapter.exchange_code_for_tokens(code)
    hub_info = hubspot_adapter.get_hub_info(tokens["access_token"])

    connection = CrmConnection(
        account_id=account_id,
        provider=CrmProvider.HUBSPOT,
        external_ref=str(hub_info["hub_id"]) if hub_info["hub_id"] is not None else hub_info["label"],
        external_account_label=hub_info["label"],
        access_token_encrypted=encrypt_secret(tokens["access_token"]),
        refresh_token_encrypted=encrypt_secret(tokens["refresh_token"]),
        token_expires_at=datetime.now(timezone.utc) + timedelta(seconds=tokens["expires_in"]),
    )
    db.add(connection)
    db.commit()
    db.refresh(connection)

    log_event(
        db, actor="hubspot_oauth_callback", action="crm_connection.connected",
        target=f"crm_connection:{connection.id}", account_id=connection.account_id,
        after={"provider": "hubspot", "real": True},
    )
    publish_crm_connected(account_id, provider="hubspot")

    from app.numbering.identity.models import Account, User, UserRole

    owner = db.query(User).filter(User.account_id == account_id, User.role == UserRole.OWNER).first()
    if owner is not None:
        account = db.query(Account).filter(Account.id == account_id).first()
        notify_integration_installed(
            db, account_id=account_id, account_email=owner.email, integration_name="HubSpot CRM",
            organization_name=account.name if account else "your organization", actor_display_name=owner.email,
        )

    return connection


def _get_valid_hubspot_access_token(db: Session, connection: CrmConnection) -> str:
    """Refreshes in place if the stored access token has expired (or is
    close enough to it - HubSpot access tokens are short-lived, ~30
    minutes), so every real sync call gets a token that's actually valid."""
    now = datetime.now(timezone.utc)
    buffer = timedelta(minutes=2)
    if connection.token_expires_at is not None and connection.token_expires_at - buffer > now:
        return decrypt_secret(connection.access_token_encrypted)

    refresh_token = decrypt_secret(connection.refresh_token_encrypted)
    tokens = hubspot_adapter.refresh_access_token(refresh_token)
    connection.access_token_encrypted = encrypt_secret(tokens["access_token"])
    connection.refresh_token_encrypted = encrypt_secret(tokens.get("refresh_token", refresh_token))
    connection.token_expires_at = now + timedelta(seconds=tokens["expires_in"])
    db.commit()
    db.refresh(connection)
    return decrypt_secret(connection.access_token_encrypted)


def build_salesforce_authorize_url(account_id: str) -> str:
    state = create_access_token(subject=account_id, scope=_SALESFORCE_OAUTH_STATE_SCOPE, expire_minutes=15)
    return salesforce_adapter.build_authorize_url(state=state)


def complete_salesforce_oauth(db: Session, *, code: str, state: str) -> CrmConnection:
    payload = decode_access_token(state)
    if payload is None or payload.get("scope") != _SALESFORCE_OAUTH_STATE_SCOPE:
        raise SalesforceOAuthStateError("Invalid or expired Salesforce OAuth state")
    account_id = payload["sub"]

    if get_connection(db, account_id) is not None:
        raise CrmAlreadyConnectedError("This account already has an active CRM connection - disconnect it first")

    tokens = salesforce_adapter.exchange_code_for_tokens(code)
    label = salesforce_adapter.get_org_label(tokens["access_token"], tokens["identity_url"])

    connection = CrmConnection(
        account_id=account_id,
        provider=CrmProvider.SALESFORCE,
        external_ref=tokens["identity_url"] or label,
        external_account_label=label,
        access_token_encrypted=encrypt_secret(tokens["access_token"]),
        refresh_token_encrypted=encrypt_secret(tokens["refresh_token"]),
        instance_url=tokens["instance_url"],
        # No stored expiry - Salesforce access tokens don't come with a
        # told-to-you lifetime, unlike HubSpot's. See
        # _call_salesforce_with_reauth for the reactive-refresh approach.
    )
    db.add(connection)
    db.commit()
    db.refresh(connection)

    log_event(
        db, actor="salesforce_oauth_callback", action="crm_connection.connected",
        target=f"crm_connection:{connection.id}", account_id=connection.account_id,
        after={"provider": "salesforce", "real": True},
    )
    publish_crm_connected(account_id, provider="salesforce")

    from app.numbering.identity.models import Account, User, UserRole

    owner = db.query(User).filter(User.account_id == account_id, User.role == UserRole.OWNER).first()
    if owner is not None:
        account = db.query(Account).filter(Account.id == account_id).first()
        notify_integration_installed(
            db, account_id=account_id, account_email=owner.email, integration_name="Salesforce CRM",
            organization_name=account.name if account else "your organization", actor_display_name=owner.email,
        )

    return connection


def _call_salesforce_with_reauth(db: Session, connection: CrmConnection, fn):
    """fn: Callable[[access_token, instance_url], T]. Salesforce access
    tokens carry no told-to-you expiry (unlike HubSpot's), so the
    documented approach is reactive: try with the stored token, and on a
    401 (SalesforceAuthExpiredError) refresh once and retry the whole
    operation exactly once with the new token."""
    access_token = decrypt_secret(connection.access_token_encrypted)
    try:
        return fn(access_token, connection.instance_url)
    except salesforce_adapter.SalesforceAuthExpiredError:
        refresh_token = decrypt_secret(connection.refresh_token_encrypted)
        tokens = salesforce_adapter.refresh_access_token(refresh_token)
        connection.access_token_encrypted = encrypt_secret(tokens["access_token"])
        connection.instance_url = tokens["instance_url"]
        db.commit()
        db.refresh(connection)
        return fn(tokens["access_token"], tokens["instance_url"])


def build_pipedrive_authorize_url(account_id: str) -> str:
    state = create_access_token(subject=account_id, scope=_PIPEDRIVE_OAUTH_STATE_SCOPE, expire_minutes=15)
    return pipedrive_adapter.build_authorize_url(state=state)


def complete_pipedrive_oauth(db: Session, *, code: str, state: str) -> CrmConnection:
    payload = decode_access_token(state)
    if payload is None or payload.get("scope") != _PIPEDRIVE_OAUTH_STATE_SCOPE:
        raise PipedriveOAuthStateError("Invalid or expired Pipedrive OAuth state")
    account_id = payload["sub"]

    if get_connection(db, account_id) is not None:
        raise CrmAlreadyConnectedError("This account already has an active CRM connection - disconnect it first")

    tokens = pipedrive_adapter.exchange_code_for_tokens(code)
    label = pipedrive_adapter.get_account_label(tokens["access_token"], tokens["api_domain"])

    connection = CrmConnection(
        account_id=account_id,
        provider=CrmProvider.PIPEDRIVE,
        external_ref=tokens["api_domain"],
        external_account_label=label,
        access_token_encrypted=encrypt_secret(tokens["access_token"]),
        refresh_token_encrypted=encrypt_secret(tokens["refresh_token"]),
        instance_url=tokens["api_domain"],
        token_expires_at=datetime.now(timezone.utc) + timedelta(seconds=tokens["expires_in"]),
    )
    db.add(connection)
    db.commit()
    db.refresh(connection)

    log_event(
        db, actor="pipedrive_oauth_callback", action="crm_connection.connected",
        target=f"crm_connection:{connection.id}", account_id=connection.account_id,
        after={"provider": "pipedrive", "real": True},
    )
    publish_crm_connected(account_id, provider="pipedrive")

    from app.numbering.identity.models import Account, User, UserRole

    owner = db.query(User).filter(User.account_id == account_id, User.role == UserRole.OWNER).first()
    if owner is not None:
        account = db.query(Account).filter(Account.id == account_id).first()
        notify_integration_installed(
            db, account_id=account_id, account_email=owner.email, integration_name="Pipedrive CRM",
            organization_name=account.name if account else "your organization", actor_display_name=owner.email,
        )

    return connection


def _get_valid_pipedrive_access_token(db: Session, connection: CrmConnection) -> str:
    """Pre-emptive refresh-before-expiry, same approach as
    _get_valid_hubspot_access_token - Pipedrive tokens carry a told-to-you
    expires_in, unlike Salesforce's."""
    now = datetime.now(timezone.utc)
    buffer = timedelta(minutes=2)
    if connection.token_expires_at is not None and connection.token_expires_at - buffer > now:
        return decrypt_secret(connection.access_token_encrypted)

    refresh_token = decrypt_secret(connection.refresh_token_encrypted)
    tokens = pipedrive_adapter.refresh_access_token(refresh_token)
    connection.access_token_encrypted = encrypt_secret(tokens["access_token"])
    connection.refresh_token_encrypted = encrypt_secret(tokens["refresh_token"])
    connection.instance_url = tokens["api_domain"]
    connection.token_expires_at = now + timedelta(seconds=tokens["expires_in"])
    db.commit()
    db.refresh(connection)
    return decrypt_secret(connection.access_token_encrypted)


def sync_contact_to_crm(db: Session, *, account_id: str, contact_id: str, name: str, phone_number: str) -> None:
    """Best-effort, same posture as ZoikoNex's usage sync and webhook
    dispatch - a no-op when there's no active connection, and never
    allowed to fail the contact create/update that triggered it."""
    connection = get_connection(db, account_id)
    if connection is None:
        return

    if _is_real_hubspot_connection(connection):
        try:
            access_token = _get_valid_hubspot_access_token(db, connection)
            result = hubspot_adapter.upsert_contact(access_token, phone_number=phone_number, name=name)
        except hubspot_adapter.HubSpotError:
            # Best-effort, same posture as the mock adapter (which can never
            # fail): a real HubSpot outage/rate-limit/expired-token must
            # never break the contact create/update that triggered this.
            logger.warning("HubSpot contact sync failed for account %s", account_id, exc_info=True)
            return
    elif _is_real_salesforce_connection(connection):
        try:
            result = _call_salesforce_with_reauth(
                db, connection,
                lambda token, instance_url: salesforce_adapter.upsert_contact(
                    token, instance_url, phone_number=phone_number, name=name
                ),
            )
        except salesforce_adapter.SalesforceError:
            logger.warning("Salesforce contact sync failed for account %s", account_id, exc_info=True)
            return
    elif _is_real_pipedrive_connection(connection):
        try:
            access_token = _get_valid_pipedrive_access_token(db, connection)
            result = pipedrive_adapter.upsert_contact(access_token, connection.instance_url, phone_number=phone_number, name=name)
        except pipedrive_adapter.PipedriveError:
            logger.warning("Pipedrive contact sync failed for account %s", account_id, exc_info=True)
            return
    else:
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

    if _is_real_hubspot_connection(connection):
        try:
            access_token = _get_valid_hubspot_access_token(db, connection)
            contact_external_ref = None
            if contact_phone:
                # No local record of a contact's HubSpot id at this call site
                # (sync_contact_to_crm doesn't persist one) - upsert is
                # idempotent, so this both resolves and guarantees the
                # contact exists before the note is associated with it.
                contact_external_ref = hubspot_adapter.upsert_contact(
                    access_token, phone_number=contact_phone, name=contact_phone
                )["external_ref"]
            result = hubspot_adapter.log_activity(
                access_token, contact_external_ref=contact_external_ref, event_type=event_type,
                note_body=f"Zoiko Local: {event_type.replace('_', ' ')}",
            )
        except hubspot_adapter.HubSpotError:
            # Best-effort - see sync_contact_to_crm's identical rationale.
            # Critically, this must never fail the notification (or, via
            # complete_hubspot_oauth's own "connected" notification, the
            # OAuth connect flow itself) that triggered it.
            logger.warning("HubSpot activity sync failed for account %s", account_id, exc_info=True)
            return
    elif _is_real_salesforce_connection(connection):
        def _do_salesforce_activity(token: str, instance_url: str) -> dict:
            contact_ref = None
            if contact_phone:
                contact_ref = salesforce_adapter.upsert_contact(
                    token, instance_url, phone_number=contact_phone, name=contact_phone
                )["external_ref"]
            return salesforce_adapter.log_activity(
                token, instance_url, contact_external_ref=contact_ref, event_type=event_type,
                description=f"Zoiko Local: {event_type.replace('_', ' ')}",
            )

        try:
            result = _call_salesforce_with_reauth(db, connection, _do_salesforce_activity)
        except salesforce_adapter.SalesforceError:
            logger.warning("Salesforce activity sync failed for account %s", account_id, exc_info=True)
            return
    elif _is_real_pipedrive_connection(connection):
        try:
            access_token = _get_valid_pipedrive_access_token(db, connection)
            contact_external_ref = None
            if contact_phone:
                contact_external_ref = pipedrive_adapter.upsert_contact(
                    access_token, connection.instance_url, phone_number=contact_phone, name=contact_phone
                )["external_ref"]
            result = pipedrive_adapter.log_activity(
                access_token, connection.instance_url, contact_external_ref=contact_external_ref,
                event_type=event_type, subject=f"Zoiko Local: {event_type.replace('_', ' ')}",
            )
        except pipedrive_adapter.PipedriveError:
            logger.warning("Pipedrive activity sync failed for account %s", account_id, exc_info=True)
            return
    else:
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
