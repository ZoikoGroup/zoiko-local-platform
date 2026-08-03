from datetime import datetime

from pydantic import BaseModel, ConfigDict


class NotificationDeliveryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    event_name: str
    recipient_email: str
    subject: str
    status: str
    error: str | None
    created_at: datetime
