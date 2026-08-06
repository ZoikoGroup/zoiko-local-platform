from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_user, require_admin
from app.crm import service
from app.crm.schemas import ConnectCrmRequest, CrmConnectionResponse, CrmSyncEventResponse
from app.numbering.identity.models import User

router = APIRouter(prefix="/crm", tags=["crm"])


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
