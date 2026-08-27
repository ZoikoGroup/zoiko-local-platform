from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.apikeys import service
from app.apikeys.schemas import ApiKeyCreatedResponse, ApiKeyResponse, CreateApiKeyRequest
from app.core.database import get_db
from app.core.deps import require_admin, require_entitlement_scope
from app.numbering.identity.models import User

router = APIRouter(prefix="/developer/api-keys", tags=["developer"])


@router.post("", response_model=ApiKeyCreatedResponse, status_code=status.HTTP_201_CREATED)
def create_api_key(
    payload: CreateApiKeyRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
    _entitlement: User = Depends(require_entitlement_scope("developer.api.scope", min_scope="limited")),
):
    try:
        key, raw_key = service.create_api_key(
            db, account_id=current_user.account_id, label=payload.label, actor=current_user.id
        )
    except service.ApiKeyLimitExceededError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e

    return ApiKeyCreatedResponse(
        id=key.id, label=key.label, key_prefix=key.key_prefix, last_used_at=key.last_used_at,
        revoked_at=key.revoked_at, created_at=key.created_at, raw_key=raw_key,
    )


@router.get("", response_model=list[ApiKeyResponse])
def list_api_keys(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    return service.list_api_keys(db, current_user.account_id)


@router.delete("/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_api_key(
    key_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    try:
        service.revoke_api_key(db, account_id=current_user.account_id, key_id=key_id, actor=current_user.id)
    except service.ApiKeyAuthorizationError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e)) from e
