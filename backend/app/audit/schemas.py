from datetime import datetime

from pydantic import BaseModel, ConfigDict


class AuditEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    actor: str
    action: str
    target: str
    before_hash: str | None
    after_hash: str | None
    reason: str | None
    correlation_id: str | None
    created_at: datetime
