from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.billing.schemas import UsageSummaryResponse
from app.billing.service import BillingSuspendedError
from app.billing import service as billing_service
from app.contacts import service as contacts_service
from app.core.database import get_db
from app.core.deps import get_api_key_account_id
from app.core.rate_limit import limiter
from app.intelligence.models import ConversationSummary
from app.media import service as media_service
from app.media.models import CallRecord, Voicemail
from app.numbering.identity.models import User, UserRole
from app.numbering.numbers.models import PhoneNumber
from app.ops.service import KillSwitchTrippedError
from app.public_api.schemas import (
    CreateContactRequest,
    PlaceCallRequest,
    PlaceCallResponse,
    PublicCallResponse,
    PublicContactResponse,
    PublicNumberResponse,
    PublicSummaryResponse,
    PublicVoicemailResponse,
)
from app.risk import service as risk_service
from app.integrations.telecom.twilio import TelecomError
from app.webhooks import service as webhooks_service
from app.webhooks.schemas import (
    CreateWebhookEndpointRequest,
    WebhookDeliveryResponse,
    WebhookEndpointCreatedResponse,
    WebhookEndpointResponse,
)

# Deliberately small and curated, per the Architecture doc's Phase 2
# posture: "Internal APIs must be designed cleanly from Phase 1, but
# public contracts should wait until domain behavior is stable." A
# subset of what already exists internally, not a mirror of every
# route. Number purchasing, routing/business-hours configuration, and
# account/team management stay behind the customer-session-only internal
# API - none of those are exposed here, deliberately, since they need
# role-scoped access control (e.g. Member vs Owner/Admin) that an API key
# alone doesn't carry. What IS exposed beyond read access: placing a call,
# saving a contact, and managing webhook subscriptions - three actions
# that make sense to automate from an external system (a script, a
# CRM-side trigger, an integration platform) without handing out
# account-management power through an API key.
router = APIRouter(prefix="/public/v1", tags=["public-api"])


def _resolve_actor_user_id(db: Session, account_id: str) -> str:
    """Webhook/audit actions need a real User row's id (audit_service.
    log_event and notification lookups key off it) - an API-key-driven
    action has no session user, so it's attributed to the account's Owner,
    the same principal an API key is scoped to serve."""
    owner = db.query(User).filter(User.account_id == account_id, User.role == UserRole.OWNER).first()
    return owner.id if owner is not None else account_id

_LIST_LIMIT = 200


@router.get("/numbers", response_model=list[PublicNumberResponse])
@limiter.limit("60/minute")
def list_numbers(
    request: Request, account_id: str = Depends(get_api_key_account_id), db: Session = Depends(get_db)
):
    return (
        db.query(PhoneNumber)
        .filter(PhoneNumber.account_id == account_id)
        .order_by(PhoneNumber.created_at.desc())
        .limit(_LIST_LIMIT)
        .all()
    )


@router.get("/calls", response_model=list[PublicCallResponse])
@limiter.limit("60/minute")
def list_calls(
    request: Request, account_id: str = Depends(get_api_key_account_id), db: Session = Depends(get_db)
):
    return (
        db.query(CallRecord)
        .filter(CallRecord.account_id == account_id)
        .order_by(CallRecord.created_at.desc())
        .limit(_LIST_LIMIT)
        .all()
    )


@router.get("/voicemails", response_model=list[PublicVoicemailResponse])
@limiter.limit("60/minute")
def list_voicemails(
    request: Request, account_id: str = Depends(get_api_key_account_id), db: Session = Depends(get_db)
):
    return (
        db.query(Voicemail)
        .filter(Voicemail.account_id == account_id)
        .order_by(Voicemail.created_at.desc())
        .limit(_LIST_LIMIT)
        .all()
    )


@router.get("/summaries", response_model=list[PublicSummaryResponse])
@limiter.limit("60/minute")
def list_summaries(
    request: Request, account_id: str = Depends(get_api_key_account_id), db: Session = Depends(get_db)
):
    return (
        db.query(ConversationSummary)
        .filter(ConversationSummary.account_id == account_id)
        .order_by(ConversationSummary.created_at.desc())
        .limit(_LIST_LIMIT)
        .all()
    )


@router.post("/calls", response_model=PlaceCallResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit("20/minute")
def place_call(
    request: Request,
    payload: PlaceCallRequest,
    account_id: str = Depends(get_api_key_account_id),
    db: Session = Depends(get_db),
):
    try:
        result = media_service.place_outbound_call_for_account(
            db, account_id=account_id, to=payload.to, from_number=payload.from_number, message=payload.message,
        )
    except media_service.CallAuthorizationError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e)) from e
    except BillingSuspendedError as e:
        raise HTTPException(status_code=status.HTTP_402_PAYMENT_REQUIRED, detail=str(e)) from e
    except risk_service.DestinationBlockedError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e)) from e
    except risk_service.VelocityLimitExceededError as e:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(e)) from e
    except risk_service.GeographicDispersionError as e:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(e)) from e
    except risk_service.SpendLimitExceededError as e:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(e)) from e
    except risk_service.ConcurrencyLimitExceededError as e:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(e)) from e
    except risk_service.CumulativeTrialUsageExceededError as e:
        raise HTTPException(status_code=status.HTTP_402_PAYMENT_REQUIRED, detail=str(e)) from e
    except risk_service.AccountKillSwitchTrippedError as e:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(e)) from e
    except KillSwitchTrippedError as e:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(e)) from e
    except TelecomError as e:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(e)) from e
    return result


@router.get("/contacts", response_model=list[PublicContactResponse])
@limiter.limit("60/minute")
def list_contacts(
    request: Request, account_id: str = Depends(get_api_key_account_id), db: Session = Depends(get_db)
):
    return contacts_service.list_contacts(db, account_id)


@router.post("/contacts", response_model=PublicContactResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit("30/minute")
def create_contact(
    request: Request,
    payload: CreateContactRequest,
    account_id: str = Depends(get_api_key_account_id),
    db: Session = Depends(get_db),
):
    return contacts_service.create_contact(
        db, account_id=account_id, user_id=None, name=payload.name, phone_number=payload.phone_number,
        email=payload.email, notes=payload.notes,
    )


@router.get("/usage", response_model=UsageSummaryResponse)
@limiter.limit("60/minute")
def usage_summary(
    request: Request, account_id: str = Depends(get_api_key_account_id), db: Session = Depends(get_db)
):
    """Read-only - this account's current billing period usage against its
    plan limits, the same data the customer-facing Billing page shows.
    No plan-change or payment action is exposed here - see this module's
    docstring for why."""
    return billing_service.get_usage_summary(db, account_id)


@router.get("/webhooks", response_model=list[WebhookEndpointResponse])
@limiter.limit("60/minute")
def list_webhooks(
    request: Request, account_id: str = Depends(get_api_key_account_id), db: Session = Depends(get_db)
):
    return webhooks_service.list_endpoints(db, account_id)


@router.post("/webhooks", response_model=WebhookEndpointCreatedResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit("10/minute")
def create_webhook(
    request: Request,
    payload: CreateWebhookEndpointRequest,
    account_id: str = Depends(get_api_key_account_id),
    db: Session = Depends(get_db),
):
    try:
        endpoint, secret = webhooks_service.create_endpoint(
            db, account_id=account_id, url=payload.url, description=payload.description,
            actor=_resolve_actor_user_id(db, account_id),
        )
    except webhooks_service.InvalidWebhookUrlError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)) from e
    except webhooks_service.WebhookEndpointLimitExceededError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e
    return WebhookEndpointCreatedResponse(
        id=endpoint.id, url=endpoint.url, description=endpoint.description, is_active=endpoint.is_active,
        created_at=endpoint.created_at, secret=secret,
    )


@router.delete("/webhooks/{endpoint_id}", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit("10/minute")
def delete_webhook(
    request: Request,
    endpoint_id: str,
    account_id: str = Depends(get_api_key_account_id),
    db: Session = Depends(get_db),
):
    try:
        webhooks_service.delete_endpoint(
            db, account_id=account_id, endpoint_id=endpoint_id, actor=_resolve_actor_user_id(db, account_id)
        )
    except webhooks_service.WebhookEndpointAuthorizationError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e


@router.get("/webhooks/deliveries", response_model=list[WebhookDeliveryResponse])
@limiter.limit("60/minute")
def list_webhook_deliveries(
    request: Request,
    endpoint_id: str | None = None,
    account_id: str = Depends(get_api_key_account_id),
    db: Session = Depends(get_db),
):
    return webhooks_service.list_deliveries(db, account_id=account_id, endpoint_id=endpoint_id)
