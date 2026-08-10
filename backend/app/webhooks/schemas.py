from datetime import datetime

from pydantic import BaseModel, ConfigDict


class CreateWebhookEndpointRequest(BaseModel):
    url: str
    description: str | None = None


class WebhookEndpointResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    url: str
    description: str | None
    is_active: bool
    created_at: datetime


class WebhookEndpointCreatedResponse(WebhookEndpointResponse):
    # Only ever present on the create response - see service.create_endpoint.
    secret: str


class WebhookDeliveryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    endpoint_id: str
    event_type: str
    status: str
    response_status_code: int | None
    error: str | None
    created_at: datetime
