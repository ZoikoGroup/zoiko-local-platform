from datetime import datetime, time

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


class NotificationPreferenceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    transactional_enabled: bool
    sms_enabled: bool
    quiet_hours_start: time | None
    quiet_hours_end: time | None
    quiet_hours_timezone: str


class NotificationPreferenceUpdate(BaseModel):
    transactional_enabled: bool | None = None
    sms_enabled: bool | None = None
    # Three-state on purpose: omitted from the request body (don't touch),
    # explicit null (clear quiet hours), or a time (set it) - the route
    # distinguishes "omitted" from "null" via model_dump(exclude_unset=True)
    # rather than a separate clear_quiet_hours flag.
    quiet_hours_start: time | None = None
    quiet_hours_end: time | None = None
    quiet_hours_timezone: str | None = None
