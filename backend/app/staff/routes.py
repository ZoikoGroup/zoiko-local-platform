from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_staff
from app.core.security import create_access_token
from app.staff import service
from app.staff.models import PlatformStaff
from app.staff.schemas import AccountOverviewResponse, StaffLoginRequest, StaffTokenResponse

router = APIRouter(prefix="/staff", tags=["staff"])

# No signup endpoint here on purpose - staff accounts are provisioned
# internally (see app/seed.py), never via public self-registration.


@router.post("/login", response_model=StaffTokenResponse)
def login(payload: StaffLoginRequest, db: Session = Depends(get_db)):
    staff = service.authenticate_staff(db, payload.email, payload.password)
    if not staff:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")
    token = create_access_token(subject=staff.id, scope="staff")
    return StaffTokenResponse(access_token=token)


@router.get("/accounts", response_model=list[AccountOverviewResponse])
def list_accounts(
    db: Session = Depends(get_db),
    _staff: PlatformStaff = Depends(get_current_staff),
):
    return service.list_accounts_overview(db)
