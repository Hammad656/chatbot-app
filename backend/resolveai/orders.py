"""Read-only order provider. URLs and credentials come only from server config."""

import os
from urllib.parse import quote, urlsplit

import httpx
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select

from database.models import DemoOrder, Workspace


class ProviderOrder(BaseModel):
    model_config = ConfigDict(extra="ignore")
    reference: str = Field(min_length=3, max_length=40)
    customer_email: str = Field(max_length=320)
    status: str = Field(min_length=1, max_length=80)
    item: str = Field(min_length=1, max_length=160)
    amount_cents: int = Field(ge=0)
    currency: str = Field(min_length=3, max_length=3)
    tracking_url: str | None = Field(default=None, max_length=1000)
    refundable: bool = False
    replaceable: bool = False


def provider_config():
    base = os.getenv("ORDER_API_BASE_URL", "").rstrip("/")
    key = os.getenv("ORDER_API_TOKEN", "")
    parsed = urlsplit(base)
    local = parsed.hostname in ("localhost", "127.0.0.1", "::1")
    if (
        not base
        or not key
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or (parsed.scheme != "https" and not (local and parsed.scheme == "http"))
    ):
        raise HTTPException(
            503,
            "Configure a valid HTTPS ORDER_API_BASE_URL and ORDER_API_TOKEN on the server before enabling the external order provider.",
        )
    return base, key


async def lookup_order(db, workspace_id, customer_email, reference):
    workspace = await db.get(Workspace, workspace_id)
    if workspace is None:
        raise HTTPException(404, "Workspace not found.")
    if workspace.settings.get("order_provider", "internal") == "external":
        base, key = provider_config()
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(8), follow_redirects=False
            ) as client:
                response = await client.get(
                    f"{base}/orders/{quote(reference, safe='')}",
                    headers={
                        "Authorization": f"Bearer {key}",
                        "X-Workspace-ID": str(workspace_id),
                    },
                )
            if response.status_code == 404:
                return {"found": False}
            response.raise_for_status()
            if len(response.content) > 32768:
                raise ValueError()
            order = ProviderOrder.model_validate(response.json())
        except (httpx.HTTPError, ValidationError, ValueError):
            raise HTTPException(
                503,
                "The authorized order service is unavailable or returned an invalid response. No order details were released.",
            ) from None
        if (
            order.reference != reference
            or order.customer_email.lower() != customer_email.lower()
        ):
            return {"found": False}
        # External financial actions remain disabled: a provider needs its own audited
        # idempotent write contract. Read integration never implies permission to pay.
        result = order.model_dump(exclude={"customer_email"})
        result.update(
            found=True, provider="external", refundable=False, replaceable=False
        )
        return result
    order = await db.scalar(
        select(DemoOrder).where(
            DemoOrder.workspace_id == workspace_id,
            DemoOrder.reference == reference,
            DemoOrder.customer_email == customer_email.lower(),
        )
    )
    if order is None:
        return {"found": False}
    return {
        "found": True,
        "provider": "internal",
        "id": str(order.id),
        **{
            name: getattr(order, name)
            for name in (
                "reference",
                "status",
                "item",
                "amount_cents",
                "currency",
                "refundable",
                "replaceable",
            )
        },
    }
