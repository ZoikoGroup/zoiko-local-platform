from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class SendMessageRequest(BaseModel):
    phone_number_id: str
    to: str
    body: str = Field(min_length=1, max_length=4096)


class MessageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    direction: str
    body: str
    status: str
    created_at: datetime


class ConversationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    phone_number_id: str
    customer_number: str
    channel: str
    opted_out: bool
    last_message_at: datetime
    created_at: datetime
