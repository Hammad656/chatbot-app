"""Bounded support preparation workflow. Mutating actions live behind review API."""

import asyncio
import re
from typing import TypedDict

from fastapi import HTTPException
from langchain_core.tools import tool
from sqlalchemy import select

from ai.gemini import GeminiLLMClient
from ai.prompts import prompt
from database.models import DemoOrder
from resolveai.knowledge import retrieve, terms
from resolveai.schemas import Draft, OrderLookup, Triage

PROMPT_VERSION = "support-v1"
QUEUES = {
    "shipping": "Fulfillment",
    "refund": "Billing",
    "replacement": "Fulfillment",
    "billing": "Billing",
    "general": "General support",
}


class SupportState(TypedDict, total=False):
    subject: str
    message: str
    customer_email: str
    order_reference: str | None
    triage: dict
    sources: list[dict]
    order: dict
    tool_calls: list[dict]
    draft: str
    proposed_action: dict


def local_triage(subject: str, message: str, reference: str | None) -> Triage:
    text = f"{subject} {message}".lower()
    if any(word in text for word in ["replace", "replacement", "damaged", "broken"]):
        category, action = "replacement", "replacement"
    elif any(word in text for word in ["refund", "return my", "money back"]):
        category, action = "refund", "refund"
    elif any(word in text for word in ["charged", "payment", "invoice", "billing"]):
        category, action = "billing", "reply"
    elif any(word in text for word in ["order", "delivery", "shipping", "tracking"]):
        category, action = "shipping", "reply"
    else:
        category, action = "general", "reply"
    found = re.search(r"\bORD-[A-Z0-9-]{3,32}\b", f"{subject} {message}".upper())
    priority = (
        "urgent"
        if "security" in text or "fraud" in text
        else "high" if "urgent" in text or "charged twice" in text else "normal"
    )
    return Triage(
        category=category,
        priority=priority,
        requested_action=action,
        order_reference=reference or (found.group() if found else None),
        reason="Local demo keyword rules; a support agent must review the classification and proposal.",
    )


def order_tool(db, workspace_id, customer_email):
    @tool("lookup_order", args_schema=OrderLookup)
    async def lookup_order(reference: str) -> dict:
        """Read a mock order in this workspace for this ticket's customer. Cannot mutate it."""
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
            "id": str(order.id),
            "reference": order.reference,
            "status": order.status,
            "item": order.item,
            "amount_cents": order.amount_cents,
            "currency": order.currency,
            "refundable": order.refundable,
            "replaceable": order.replaceable,
        }

    return lookup_order


class SupportEngine:
    def __init__(self, mode, generate, configured_model, map_error, settings=None):
        self.mode = mode
        self.settings = settings or {}
        self.generate = generate
        self.configured_model = configured_model
        self.map_error = map_error
        self.ai = GeminiLLMClient(generate, configured_model, map_error)
        self.tokens = 0
        self.model = "local-rules" if mode == "local" else configured_model()

    async def structured(self, schema, instruction, data):
        try:
            return await self.ai.complete_structured(schema, instruction, data)
        finally:
            self.tokens, self.model = self.ai.tokens, self.ai.model

    async def graph(
        self, db, workspace_id, initial: SupportState, session_factory=None
    ):
        if session_factory is not None:
            from database.tenant import set_tenant

            async with session_factory() as session:
                await set_tenant(session, workspace_id)
                return await self.graph(session, workspace_id, initial)

        async def classify(state):
            if self.mode == "local":
                triage = local_triage(
                    state["subject"], state["message"], state.get("order_reference")
                )
            else:
                triage = await self.structured(
                    Triage,
                    prompt("classify_v1"),
                    state,
                )
                if state.get("order_reference"):
                    triage.order_reference = state["order_reference"]
            return {"triage": triage.model_dump()}

        async def knowledge(state):
            sources = await retrieve(
                db,
                workspace_id,
                f"{state['triage']['category']} {state['subject']} {state['message']}",
                self.mode,
            )
            return {"sources": sources}

        async def tools(state):
            reference = state["triage"].get("order_reference")
            if not reference:
                return {"order": {"found": False}, "tool_calls": []}
            result = await order_tool(
                db, workspace_id, state["customer_email"]
            ).ainvoke({"reference": reference})
            return {
                "order": result,
                "tool_calls": [
                    {
                        "name": "lookup_order",
                        "arguments": {"reference": reference},
                        "result": result,
                    }
                ],
            }

        async def draft(state):
            order, sources = state["order"], state["sources"]
            requested = state["triage"]["requested_action"]
            action = "reply"
            rationale = (
                "The proposed reply requires human review before simulated delivery."
            )
            if requested in ("refund", "replacement"):
                evidence_words = (
                    {"refund", "refunds", "return", "returns"}
                    if requested == "refund"
                    else {"replace", "replacement", "damaged"}
                )
                policy_evidence = any(
                    terms(source["text"]) & evidence_words for source in sources
                )
                eligible = (
                    order.get("found")
                    and policy_evidence
                    and order.get("status") in ("delivered", "shipped")
                    and order.get(
                        "refundable" if requested == "refund" else "replaceable"
                    )
                )
                action = requested if eligible else "escalate"
                rationale = (
                    "Authorized review is required; policy evidence and the current order are rechecked at approval."
                    if eligible
                    else "Escalate: verified policy/order eligibility is insufficient."
                )
            elif requested == "escalate" or (not sources and not order.get("found")):
                action = "escalate"
            if self.mode == "gemini" and (sources or order.get("found")):
                result = await self.structured(
                    Draft,
                    prompt("draft_v1"),
                    {
                        **state,
                        "server_proposed_action": action,
                        "tone": self.settings.get("tone", "professional"),
                    },
                )
                valid_ids = {source["id"] for source in sources}
                inline_ids = {
                    int(value) for value in re.findall(r"\[(\d+)\]", result.reply)
                }
                if not set(result.source_ids).issubset(
                    valid_ids
                ) or not inline_ids.issubset(valid_ids):
                    raise HTTPException(
                        502,
                        "Draft contained an unsupported citation. Retry or escalate for manual handling.",
                    )
                if sources and not order.get("found") and not result.source_ids:
                    raise HTTPException(
                        502,
                        "Draft omitted policy evidence. Retry or escalate for manual handling.",
                    )
                reply = result.reply
            else:
                pieces = ["Thank you for contacting support."]
                if order.get("found"):
                    pieces.append(
                        f"Our order records show {order['reference']} ({order['item']}) is {order['status'].replace('_', ' ')}."
                    )
                if sources:
                    pieces.append(f"Our policy states: “{sources[0]['text']}” [1]")
                if action in ("refund", "replacement"):
                    pieces.append(
                        f"A {action} is proposed for authorized review; it has not been processed yet."
                    )
                elif action == "escalate":
                    pieces.append(
                        "A support agent needs to review this request because the available evidence is insufficient for an automatic resolution."
                    )
                reply = "\n\n".join(pieces)
            proposal = {"kind": action, "rationale": rationale}
            if action in ("refund", "replacement"):
                proposal.update(
                    {
                        "order_id": order["id"],
                        "reference": order["reference"],
                        "amount_cents": order["amount_cents"],
                        "currency": order["currency"],
                    }
                )
            return {"draft": reply, "proposed_action": proposal}

        # The system design uses a bounded ordinary-Python orchestration loop.
        # No LLM is allowed to execute a mutation; approval lives in the review service.
        state = dict(initial)
        async with asyncio.timeout(30):
            for node in (classify, knowledge, tools, draft):
                state.update(await node(state))
        return state

    async def answer(self, question, sources, history=None):
        if not sources:
            return "I could not find supporting information in this workspace's ready documents. Add the relevant policy or ask a more specific question."
        if self.mode == "local":
            return "Local demo · matching policy excerpts:\n\n" + "\n\n".join(
                f"[{s['id']}] {s['text']}" for s in sources
            )
        result = await self.structured(
            Draft,
            prompt("answer_v1"),
            {
                "question": question,
                "sources": sources,
                "conversation_context": history or [],
                "tone": self.settings.get("tone", "professional"),
            },
        )
        valid = {source["id"] for source in sources}
        if (
            not result.source_ids
            or not set(result.source_ids).issubset(valid)
            or any(
                int(value) not in valid
                for value in re.findall(r"\[(\d+)\]", result.reply)
            )
        ):
            raise HTTPException(
                502,
                "The answer did not include valid supporting citations. Please retry.",
            )
        return result.reply
