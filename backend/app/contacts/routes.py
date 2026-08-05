from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.contacts import service
from app.contacts.schemas import (
    ContactCreateRequest,
    ContactHistoryEntry,
    ContactResponse,
    ContactUpdateRequest,
)
from app.core.database import get_db
from app.core.deps import get_current_user
from app.numbering.identity.models import User

router = APIRouter(prefix="/contacts", tags=["contacts"])


@router.post("", response_model=ContactResponse, status_code=status.HTTP_201_CREATED)
def create_contact(
    payload: ContactCreateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        return service.create_contact(
            db, account_id=current_user.account_id, name=payload.name, phone_number=payload.phone_number,
            email=payload.email, notes=payload.notes,
        )
    except service.ContactConflictError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e


@router.get("", response_model=list[ContactResponse])
def list_contacts(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return service.list_contacts(db, account_id=current_user.account_id)


@router.get("/{contact_id}", response_model=ContactResponse)
def get_contact(
    contact_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        return service.get_contact(db, account_id=current_user.account_id, contact_id=contact_id)
    except service.ContactNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e


@router.put("/{contact_id}", response_model=ContactResponse)
def update_contact(
    contact_id: str,
    payload: ContactUpdateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        return service.update_contact(
            db, account_id=current_user.account_id, contact_id=contact_id, name=payload.name,
            phone_number=payload.phone_number, email=payload.email, notes=payload.notes,
        )
    except service.ContactNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except service.ContactConflictError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e


@router.delete("/{contact_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_contact(
    contact_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        service.delete_contact(db, account_id=current_user.account_id, contact_id=contact_id)
    except service.ContactNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e


@router.get("/{contact_id}/history", response_model=list[ContactHistoryEntry])
def get_contact_history(
    contact_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        return service.get_contact_history(db, account_id=current_user.account_id, contact_id=contact_id)
    except service.ContactNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
