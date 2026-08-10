from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_user, require_admin
from app.numbering.identity.models import User
from app.webhooks import service
from app.webhooks.schemas import (
    CreateWebhookEndpointRequest,
    WebhookDeliveryResponse,
    WebhookEndpointCreatedResponse,
    WebhookEndpointResponse,
)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.post("/endpoints", response_model=WebhookEndpointCreatedResponse, status_code=status.HTTP_201_CREATED)
def create_endpoint(
    payload: CreateWebhookEndpointRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    try:
        endpoint, secret = service.create_endpoint(
            db, account_id=current_user.account_id, url=payload.url, description=payload.description,
            actor=current_user.id,
        )
    except service.InvalidWebhookUrlError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)) from e
    except service.WebhookEndpointLimitExceededError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e

    return WebhookEndpointCreatedResponse(
        id=endpoint.id, url=endpoint.url, description=endpoint.description, is_active=endpoint.is_active,
        created_at=endpoint.created_at, secret=secret,
    )


@router.get("/endpoints", response_model=list[WebhookEndpointResponse])
def list_endpoints(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return service.list_endpoints(db, current_user.account_id)


@router.delete("/endpoints/{endpoint_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_endpoint(
    endpoint_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    try:
        service.delete_endpoint(db, account_id=current_user.account_id, endpoint_id=endpoint_id, actor=current_user.id)
    except service.WebhookEndpointAuthorizationError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e)) from e


@router.get("/deliveries", response_model=list[WebhookDeliveryResponse])
def list_deliveries(
    endpoint_id: str | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return service.list_deliveries(db, account_id=current_user.account_id, endpoint_id=endpoint_id)
