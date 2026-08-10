from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ConnectCrmRequest(BaseModel):
    provider: str


class HubSpotAuthorizeResponse(BaseModel):
    authorize_url: str


class SalesforceAuthorizeResponse(BaseModel):
    authorize_url: str


class PipedriveAuthorizeResponse(BaseModel):
    authorize_url: str


class CrmConnectionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    provider: str
    external_account_label: str
    connected_at: datetime


class CrmSyncEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    event_type: str
    external_ref: str
    payload: dict
    created_at: datetime
