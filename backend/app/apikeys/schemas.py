from datetime import datetime

from pydantic import BaseModel, ConfigDict


class CreateApiKeyRequest(BaseModel):
    label: str


class ApiKeyResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    label: str
    key_prefix: str
    last_used_at: datetime | None
    revoked_at: datetime | None
    created_at: datetime


class ApiKeyCreatedResponse(ApiKeyResponse):
    # Only ever present on the create response - see service.create_api_key.
    raw_key: str
