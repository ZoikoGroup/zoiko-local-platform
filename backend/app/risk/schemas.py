from datetime import datetime

from pydantic import BaseModel, ConfigDict


class BlockedDestinationCreate(BaseModel):
    prefix: str
    reason: str


class BlockedDestinationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    prefix: str
    reason: str
    created_at: datetime
