from datetime import datetime

from pydantic import BaseModel, ConfigDict


class UsageEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    event_type: str
    quantity: float
    unit: str
    country_band: str | None
    created_at: datetime
