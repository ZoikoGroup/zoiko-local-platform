from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.deps import get_current_user, require_admin
from app.crm import service
from app.crm.schemas import (
    ConnectCrmRequest,
    CrmConnectionResponse,
    CrmSyncEventResponse,
    HubSpotAuthorizeResponse,
    PipedriveAuthorizeResponse,
    SalesforceAuthorizeResponse,
)
from app.integrations.crm import hubspot as hubspot_adapter
from app.integrations.crm import pipedrive as pipedrive_adapter
from app.integrations.crm import salesforce as salesforce_adapter
from app.numbering.identity.models import User

router = APIRouter(prefix="/crm", tags=["crm"])
# The CRM Connection UI lives on the Business/Integrations dashboard page
# at this path (frontend/src/app/dashboard/business/page.tsx) - both OAuth
# callbacks redirect the browser back here, success or failure.
_INTEGRATIONS_PAGE_URL = "/dashboard/business"


@router.post("/connect", response_model=CrmConnectionResponse, status_code=status.HTTP_201_CREATED)
def connect_crm(
    payload: ConnectCrmRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    try:
        return service.connect_crm(
            db, account_id=current_user.account_id, provider=payload.provider, actor=current_user.id
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)) from e
    except service.CrmAlreadyConnectedError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e


@router.get("/hubspot/authorize", response_model=HubSpotAuthorizeResponse)
def hubspot_authorize(current_user: User = Depends(require_admin)):
    """Owner/Admin only, matching every other CRM connection action. Returns
    the URL for the frontend to redirect the browser to - HubSpot's own
    consent screen is what the customer actually authenticates against."""
    try:
        return {"authorize_url": service.build_hubspot_authorize_url(current_user.account_id)}
    except hubspot_adapter.HubSpotError as e:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(e)) from e


@router.get("/hubspot/callback")
def hubspot_callback(code: str, state: str, db: Session = Depends(get_db)):
    """HubSpot redirects the customer's browser here after they approve the
    connection - unauthenticated by user session (there's no bearer token
    on this request), trust comes entirely from the signed `state`. Always
    ends in a redirect back to the frontend Integrations page, success or
    failure, since a browser (not an API client) lands here."""
    integrations_url = f"{settings.frontend_base_url}{_INTEGRATIONS_PAGE_URL}"
    try:
        service.complete_hubspot_oauth(db, code=code, state=state)
    except (service.HubSpotOAuthStateError, service.CrmAlreadyConnectedError, hubspot_adapter.HubSpotError):
        return RedirectResponse(url=f"{integrations_url}?crm=error")
    return RedirectResponse(url=f"{integrations_url}?crm=connected")


@router.get("/salesforce/authorize", response_model=SalesforceAuthorizeResponse)
def salesforce_authorize(current_user: User = Depends(require_admin)):
    try:
        return {"authorize_url": service.build_salesforce_authorize_url(current_user.account_id)}
    except salesforce_adapter.SalesforceError as e:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(e)) from e


@router.get("/salesforce/callback")
def salesforce_callback(code: str, state: str, db: Session = Depends(get_db)):
    """Same shape as hubspot_callback above - see that route's docstring."""
    integrations_url = f"{settings.frontend_base_url}{_INTEGRATIONS_PAGE_URL}"
    try:
        service.complete_salesforce_oauth(db, code=code, state=state)
    except (service.SalesforceOAuthStateError, service.CrmAlreadyConnectedError, salesforce_adapter.SalesforceError):
        return RedirectResponse(url=f"{integrations_url}?crm=error")
    return RedirectResponse(url=f"{integrations_url}?crm=connected")


@router.get("/pipedrive/authorize", response_model=PipedriveAuthorizeResponse)
def pipedrive_authorize(current_user: User = Depends(require_admin)):
    try:
        return {"authorize_url": service.build_pipedrive_authorize_url(current_user.account_id)}
    except pipedrive_adapter.PipedriveError as e:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(e)) from e


@router.get("/pipedrive/callback")
def pipedrive_callback(code: str, state: str, db: Session = Depends(get_db)):
    """Same shape as hubspot_callback above - see that route's docstring."""
    integrations_url = f"{settings.frontend_base_url}{_INTEGRATIONS_PAGE_URL}"
    try:
        service.complete_pipedrive_oauth(db, code=code, state=state)
    except (service.PipedriveOAuthStateError, service.CrmAlreadyConnectedError, pipedrive_adapter.PipedriveError):
        return RedirectResponse(url=f"{integrations_url}?crm=error")
    return RedirectResponse(url=f"{integrations_url}?crm=connected")


@router.post("/disconnect", status_code=status.HTTP_204_NO_CONTENT)
def disconnect_crm(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    try:
        service.disconnect_crm(db, account_id=current_user.account_id, actor=current_user.id)
    except service.CrmNotConnectedError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e


@router.get("/connection", response_model=CrmConnectionResponse | None)
def get_connection(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return service.get_connection(db, current_user.account_id)


@router.get("/sync-log", response_model=list[CrmSyncEventResponse])
def list_sync_events(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return service.list_sync_events(db, current_user.account_id)
