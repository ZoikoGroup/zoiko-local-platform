from fastapi import APIRouter, Depends

from app.core.deps import get_current_staff
from app.ops import service
from app.staff.models import PlatformStaff

router = APIRouter(prefix="/ops", tags=["ops"])


@router.get("/provider-status")
async def provider_status(_staff: PlatformStaff = Depends(get_current_staff)):
    """Any staff role can view this - it's diagnostic, not an approval
    action, so it doesn't need the SUPPORT/COMPLIANCE_OFFICER/SUPER_ADMIN
    segregation that KYC decisions do."""
    return {"providers": await service.get_provider_statuses()}


@router.get("/status")
async def public_status():
    """No auth - this is the customer-facing status page the marketing site
    links to. Deliberately separate from /provider-status: never leaks
    provider names or raw error detail, only named components and a plain
    operational/degraded status."""
    return await service.get_public_status()
