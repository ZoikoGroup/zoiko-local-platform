from datetime import datetime

from pydantic import BaseModel, ConfigDict


class NotificationDeliveryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    event_name: str
    channel: str
    recipient_email: str | None
    recipient_phone: str | None
    subject: str
    status: str
    error: str | None
    created_at: datetime
    read_at: datetime | None


class UnreadCountResponse(BaseModel):
    unread_count: int


class PushSubscribeRequest(BaseModel):
    endpoint: str
    p256dh: str
    auth: str


class PushUnsubscribeRequest(BaseModel):
    endpoint: str


class PushSubscriptionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    endpoint: str
    created_at: datetime
