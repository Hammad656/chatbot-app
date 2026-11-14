from typing import Literal
from uuid import UUID

from pydantic import EmailStr, Field

from resolveai.schemas import StrictModel


class OpenSession(StrictModel):
    workspace_key: str = Field(
        min_length=16, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$"
    )
    access_token: str | None = Field(
        default=None, min_length=32, max_length=128, pattern=r"^[a-zA-Z0-9_-]+$"
    )
    site_origin: str | None = Field(default=None, max_length=500)


class Message(StrictModel):
    content: str = Field(min_length=1, max_length=4000)
    request_id: UUID


class Feedback(StrictModel):
    message_id: UUID
    vote: Literal[-1, 1]


class AccessLink(StrictModel):
    email: EmailStr
    name: str = Field(default="Customer", min_length=1, max_length=100)


class ChatRoute(StrictModel):
    intent: Literal["knowledge", "order", "escalation"]
    confidence: float = Field(ge=0, le=1)
    category: Literal["shipping", "refund", "replacement", "billing", "general"]
    priority: Literal["low", "normal", "high", "urgent"]
    order_reference: str | None = Field(
        default=None, pattern=r"^[A-Z0-9][A-Z0-9-]{2,39}$"
    )
    requested_action: Literal["reply", "refund", "replacement", "escalate"] = "reply"
    reason: str = Field(min_length=1, max_length=1000)
