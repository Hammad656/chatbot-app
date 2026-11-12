from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class WorkspaceCreate(StrictModel):
    name: str = Field(min_length=2, max_length=100)
    ai_mode: Literal["local", "gemini"] = "local"


class MemberCreate(StrictModel):
    email: EmailStr
    role: Literal["admin", "agent"] = "agent"


class TicketCreate(StrictModel):
    subject: str = Field(min_length=3, max_length=160)
    customer_email: EmailStr
    description: str = Field(min_length=5, max_length=12000)
    order_reference: str | None = Field(
        default=None, pattern=r"^[A-Z0-9][A-Z0-9-]{2,39}$"
    )


class Question(StrictModel):
    question: str = Field(min_length=3, max_length=4000)


class Review(StrictModel):
    request_id: UUID
    expected_revision: int = Field(ge=1)
    decision: Literal["approve", "reject"]
    reply: str = Field(min_length=1, max_length=12000)


class Feedback(StrictModel):
    vote: Literal[-1, 1]
    comment: str = Field(default="", max_length=1000)


class OrderLookup(StrictModel):
    reference: str = Field(pattern=r"^[A-Z0-9][A-Z0-9-]{2,39}$")


class Triage(StrictModel):
    category: Literal["shipping", "refund", "replacement", "billing", "general"]
    priority: Literal["low", "normal", "high", "urgent"]
    requested_action: Literal["reply", "refund", "replacement", "escalate"]
    order_reference: str | None = Field(
        default=None, pattern=r"^[A-Z0-9][A-Z0-9-]{2,39}$"
    )
    reason: str = Field(min_length=1, max_length=1000)


class Draft(StrictModel):
    reply: str = Field(min_length=1, max_length=12000)
    source_ids: list[int] = Field(default_factory=list, max_length=5)
    rationale: str = Field(min_length=1, max_length=1000)


class OrderCreate(StrictModel):
    reference: str = Field(pattern=r"^[A-Z0-9][A-Z0-9-]{2,39}$")
    customer_email: EmailStr
    item: str = Field(min_length=1, max_length=160)
    status: Literal["processing", "shipped", "delivered"] = "processing"
    amount_cents: int = Field(gt=0, le=10000000)
    currency: str = Field(default="USD", pattern=r"^[A-Z]{3}$")
    refundable: bool = False
    replaceable: bool = False
