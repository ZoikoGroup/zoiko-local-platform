from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_user, require_entitlement, require_writer
from app.numbering.identity.models import User
from app.routing import service
from app.routing.schemas import (
    AssignCallFlowRequest,
    CallFlowResponse,
    CallFlowSummary,
    CallFlowVersionResponse,
    CreateCallFlowRequest,
    PublishResult,
    RollbackRequest,
    SaveDraftRequest,
)
from app.routing.service import CallFlowNotFoundError, NumberNotOwnedError

router = APIRouter(prefix="/call-flows", tags=["call-flows"])


@router.post("", response_model=CallFlowSummary, status_code=status.HTTP_201_CREATED)
def create_call_flow(
    payload: CreateCallFlowRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_writer),
    _entitlement: User = Depends(require_entitlement("routing.advanced")),
):
    flow = service.create_flow(db, current_user.account_id, payload.name, current_user.id)
    return {"id": flow.id, "name": flow.name, "created_at": flow.created_at, "has_draft": True,
            "live_version": None, "assigned_numbers": []}


@router.get("", response_model=list[CallFlowSummary])
def list_call_flows(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return service.list_flows(db, current_user.account_id)


@router.get("/{call_flow_id}", response_model=CallFlowResponse)
def get_call_flow(
    call_flow_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        return service.get_flow_detail(db, current_user.account_id, call_flow_id)
    except CallFlowNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e


@router.put("/{call_flow_id}/draft", response_model=CallFlowVersionResponse)
def save_draft(
    call_flow_id: str,
    payload: SaveDraftRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_writer),
):
    try:
        # mode="json" - a business_hours node's start/end are pydantic `time`
        # values; the JSON column these land in (call_flow_versions.nodes)
        # can't serialize a raw datetime.time, so they must become ISO
        # strings ("09:00:00") before they're ever handed to SQLAlchemy.
        # service._resolve() parses them back with time.fromisoformat().
        return service.save_draft(
            db, current_user.account_id, call_flow_id, payload.entry_node_id,
            [node.model_dump(mode="json") for node in payload.nodes],
        )
    except CallFlowNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e


@router.post("/{call_flow_id}/validate", response_model=PublishResult)
def validate_draft(
    call_flow_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        detail = service.get_flow_detail(db, current_user.account_id, call_flow_id)
    except CallFlowNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    draft = detail["draft"]
    errors = service.validate_flow(draft.nodes, draft.entry_node_id, service.account_queue_ids(db, current_user.account_id))
    return {"published": False, "errors": errors, "version": None}


@router.post("/{call_flow_id}/publish", response_model=PublishResult)
def publish_call_flow(
    call_flow_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_writer),
    _entitlement: User = Depends(require_entitlement("routing.advanced")),
):
    try:
        published, errors, version = service.publish_flow(db, current_user.account_id, call_flow_id, current_user.id)
    except CallFlowNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    return {"published": published, "errors": errors, "version": version}


@router.post("/{call_flow_id}/rollback", response_model=CallFlowVersionResponse)
def rollback_call_flow(
    call_flow_id: str,
    payload: RollbackRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_writer),
):
    try:
        return service.rollback_flow(db, current_user.account_id, call_flow_id, payload.version, current_user.id)
    except CallFlowNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e


@router.post("/{call_flow_id}/assign")
def assign_call_flow(
    call_flow_id: str,
    payload: AssignCallFlowRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_writer),
):
    try:
        number = service.assign_to_number(db, current_user.account_id, call_flow_id, payload.phone_number_id)
    except CallFlowNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except NumberNotOwnedError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"number {e} not found") from e
    return {"phone_number_id": number.id, "call_flow_id": number.call_flow_id}


@router.post("/unassign/{phone_number_id}")
def unassign_call_flow(
    phone_number_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_writer),
):
    try:
        number = service.assign_to_number(db, current_user.account_id, None, phone_number_id)
    except NumberNotOwnedError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"number {e} not found") from e
    return {"phone_number_id": number.id, "call_flow_id": number.call_flow_id}
