from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.contacts import service
from app.contacts.schemas import ContactCreate, ContactHistoryEntry, ContactResponse, ContactUpdate
from app.core.database import get_db
from app.core.deps import get_current_user, require_writer
from app.numbering.identity.models import User

router = APIRouter(prefix="/contacts", tags=["contacts"])


@router.get("", response_model=list[ContactResponse])
def list_contacts(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Account-wide address book - any team member (including Viewer) can
    see it, same as Numbers/Calls. Only create/update/delete require
    require_writer."""
    return service.list_contacts(db, current_user.account_id)


@router.post("", response_model=ContactResponse, status_code=status.HTTP_201_CREATED)
def create_contact(
    payload: ContactCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_writer),
):
    return service.create_contact(
        db, account_id=current_user.account_id, user_id=current_user.id,
        name=payload.name, phone_number=payload.phone_number, email=payload.email, notes=payload.notes,
    )


@router.get("/{contact_id}", response_model=ContactResponse)
def get_contact(
    contact_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        return service.get_contact(db, current_user.account_id, contact_id)
    except service.ContactNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e


@router.put("/{contact_id}", response_model=ContactResponse)
def update_contact(
    contact_id: str,
    payload: ContactUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_writer),
):
    try:
        return service.update_contact(
            db, account_id=current_user.account_id, user_id=current_user.id, contact_id=contact_id,
            name=payload.name, phone_number=payload.phone_number, email=payload.email, notes=payload.notes,
        )
    except service.ContactNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e


@router.delete("/{contact_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_contact(
    contact_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_writer),
):
    try:
        service.delete_contact(db, account_id=current_user.account_id, user_id=current_user.id, contact_id=contact_id)
    except service.ContactNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e


@router.get("/{contact_id}/history", response_model=list[ContactHistoryEntry])
def get_contact_history(
    contact_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        contact = service.get_contact(db, current_user.account_id, contact_id)
    except service.ContactNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e

    return service.get_contact_history(db, current_user, contact)
