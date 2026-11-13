from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, field_validator

from resolveai.schemas import MemberCreate, StrictModel


class Settings(StrictModel):
    tone: Literal["professional", "friendly", "concise"] = "professional"
    similarity_threshold: float = Field(default=0.35, ge=0, le=1)


class WorkspaceUpdate(StrictModel):
    name: str | None = Field(default=None, min_length=2, max_length=100)
    allowed_origins: list[str] | None = Field(default=None, max_length=20)
    settings: Settings | None = None

    @field_validator("allowed_origins")
    @classmethod
    def validate_origins(cls, origins):
        if origins is None:
            return None
        validated = []
        for origin in origins:
            parsed = urlsplit(origin.strip())
            try:
                valid_port = parsed.port
            except ValueError:
                raise ValueError(
                    "Use a valid origin such as https://support.example.com."
                ) from None
            if (
                parsed.scheme not in ("http", "https")
                or not parsed.hostname
                or parsed.username
                or parsed.password
                or parsed.query
                or parsed.fragment
                or parsed.path not in ("", "/")
                or "*" in origin
            ):
                raise ValueError(
                    "Allowed origins must be exact http(s) origins without paths or wildcards."
                )
            host = parsed.hostname.lower()
            if ":" in host:
                host = f"[{host}]"
            value = f"{parsed.scheme}://{host}" + (
                f":{valid_port}" if valid_port else ""
            )
            if value not in validated:
                validated.append(value)
        return validated


class MemberUpdate(StrictModel):
    role: Literal["admin", "agent"]


class InvitationCreate(MemberCreate):
    pass


class InvitationAccept(StrictModel):
    token: str = Field(min_length=32, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")


class WorkspaceDelete(StrictModel):
    confirmation_name: str = Field(min_length=2, max_length=100)
