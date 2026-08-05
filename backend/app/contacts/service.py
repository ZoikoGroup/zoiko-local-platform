from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.audit.service import log_event
from app.contacts.models import Contact
from app.contacts.schemas import ContactHistoryEntry
from app.media.models import CallRecord, ReceptionistCall, Voicemail


class ContactConflictError(Exception):
    """Raised when an account already has a contact for this phone number."""


class ContactNotFoundError(Exception):
    """Raised when a contact id doesn't exist, or belongs to another
    account - a contact from another account is indistinguishable from a
    nonexistent one to the caller, same rationale as team_routes.py's
    remove_team_member."""


def create_contact(
    db: Session, *, account_id: str, name: str, phone_number: str, email: str | None = None,
    notes: str | None = None,
) -> Contact:
    existing = (
        db.query(Contact)
        .filter(Contact.account_id == account_id, Contact.phone_number == phone_number)
        .first()
    )
    if existing is not None:
        raise ContactConflictError(f"A contact for {phone_number} already exists on this account")

    contact = Contact(account_id=account_id, name=name, phone_number=phone_number, email=email, notes=notes)
    db.add(contact)
    db.commit()
    db.refresh(contact)
    log_event(db, actor_id=account_id, action="contact.created", target_type="contact", target_id=contact.id)
    return contact


def list_contacts(db: Session, *, account_id: str) -> list[Contact]:
    return db.query(Contact).filter(Contact.account_id == account_id).order_by(Contact.name).all()


def get_contact(db: Session, *, account_id: str, contact_id: str) -> Contact:
    contact = db.query(Contact).filter(Contact.id == contact_id).first()
    if contact is None or contact.account_id != account_id:
        raise ContactNotFoundError(f"No such contact {contact_id!r} on this account")
    return contact


def update_contact(
    db: Session, *, account_id: str, contact_id: str, name: str, phone_number: str,
    email: str | None = None, notes: str | None = None,
) -> Contact:
    contact = get_contact(db, account_id=account_id, contact_id=contact_id)

    if phone_number != contact.phone_number:
        conflict = (
            db.query(Contact)
            .filter(Contact.account_id == account_id, Contact.phone_number == phone_number)
            .first()
        )
        if conflict is not None:
            raise ContactConflictError(f"A contact for {phone_number} already exists on this account")

    contact.name = name
    contact.phone_number = phone_number
    contact.email = email
    contact.notes = notes
    db.commit()
    db.refresh(contact)
    log_event(db, actor_id=account_id, action="contact.updated", target_type="contact", target_id=contact.id)
    return contact


def delete_contact(db: Session, *, account_id: str, contact_id: str) -> None:
    contact = get_contact(db, account_id=account_id, contact_id=contact_id)
    db.delete(contact)
    db.commit()
    log_event(db, actor_id=account_id, action="contact.deleted", target_type="contact", target_id=contact_id)


def get_contact_history(db: Session, *, account_id: str, contact_id: str) -> list[ContactHistoryEntry]:
    contact = get_contact(db, account_id=account_id, contact_id=contact_id)
    phone = contact.phone_number

    calls = (
        db.query(CallRecord)
        .filter(
            CallRecord.account_id == account_id,
            or_(CallRecord.from_number == phone, CallRecord.to_number == phone),
        )
        .all()
    )
    voicemails = (
        db.query(Voicemail)
        .filter(Voicemail.account_id == account_id, Voicemail.from_number == phone)
        .all()
    )
    receptionist_calls = (
        db.query(ReceptionistCall)
        .filter(ReceptionistCall.account_id == account_id, ReceptionistCall.caller_number == phone)
        .all()
    )

    entries = [
        ContactHistoryEntry(
            type="call", id=c.id, direction=c.direction.value, status=c.status, duration=c.duration,
            recording_url=c.recording_url, created_at=c.created_at,
        )
        for c in calls
    ] + [
        ContactHistoryEntry(
            type="voicemail", id=v.id, duration=v.duration, recording_url=v.recording_url,
            created_at=v.created_at,
        )
        for v in voicemails
    ] + [
        ContactHistoryEntry(
            type="receptionist_call", id=r.id, summary=r.summary, status=r.urgency.value if r.urgency else None,
            created_at=r.created_at,
        )
        for r in receptionist_calls
    ]

    entries.sort(key=lambda e: e.created_at, reverse=True)
    return entries
