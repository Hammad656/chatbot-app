"""Exercise the real SDK serialization against a local HTTP transport."""

import json
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest
from fastapi import HTTPException
from google import genai
from google.genai import types
from pydantic import ValidationError

from ai.gemini import _gemini_http_error, _generate_gemini_content
from check_gemini import check
from resolveai.schemas import Draft, StrictModel, Triage, gemini_response_schema
from resolveai.workflow import SupportEngine


def has_unsupported_keyword(value):
    if isinstance(value, dict):
        return any(
            key in ("additionalProperties", "additional_properties")
            or has_unsupported_keyword(child)
            for key, child in value.items()
        )
    if isinstance(value, list):
        return any(has_unsupported_keyword(child) for child in value)
    return False


def sdk_provider(monkeypatch, answer):
    """Reject exactly the unsupported field reported by the real provider."""
    requests = []

    def handle(request):
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "models": [
                        {
                            "name": "models/gemini-3.8-flash",
                            "supportedGenerationMethods": ["generateContent"],
                        }
                    ]
                },
            )
        body = json.loads(request.content)
        requests.append(body)
        if has_unsupported_keyword(body["generationConfig"]["responseSchema"]):
            return httpx.Response(
                400,
                json={
                    "error": {
                        "code": 400,
                        "status": "INVALID_ARGUMENT",
                        "message": "Invalid JSON payload received. Unknown name "
                        '"additional_properties" at generation_config.response_schema: Cannot find field.',
                    }
                },
            )
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "content": {
                            "role": "model",
                            "parts": [{"text": json.dumps(answer)}],
                        },
                        "finishReason": "STOP",
                    }
                ],
                "usageMetadata": {"totalTokenCount": 24},
                "modelVersion": "gemini-3.8-flash",
            },
        )

    monkeypatch.setenv("GEMINI_API_KEY", "synthetic-structured-test-key")
    monkeypatch.setenv("GEMINI_MODEL", "gemini-3.8-flash")
    client = genai.Client(
        api_key="synthetic-structured-test-key",
        http_options=types.HttpOptions(
            async_client_args={
                "transport": httpx.MockTransport(handle),
                "trust_env": False,
            }
        ),
    )
    factory = Mock(return_value=client)
    monkeypatch.setattr("ai.gemini.genai.Client", factory)
    return SimpleNamespace(requests=requests, factory=factory)


@pytest.mark.parametrize(
    "schema,answer",
    [
        (
            Draft,
            {
                "reply": "Known policy [1]",
                "source_ids": [1],
                "rationale": "Policy evidence",
            },
        ),
        (
            Triage,
            {
                "category": "refund",
                "priority": "normal",
                "requested_action": "refund",
                "order_reference": None,
                "reason": "Customer requested a refund",
            },
        ),
    ],
)
async def test_real_sdk_structured_requests_use_supported_schema(
    monkeypatch, schema, answer
):
    provider = sdk_provider(monkeypatch, answer)
    engine = SupportEngine(
        "gemini",
        _generate_gemini_content,
        lambda: "gemini-3.8-flash",
        _gemini_http_error,
    )
    result = await engine.structured(
        schema, "Use the supplied policy.", {"question": "Test"}
    )
    assert result == schema.model_validate(answer)
    assert engine.tokens == 24
    assert len(provider.requests) == 1
    sent = provider.requests[0]["generationConfig"]["responseSchema"]
    assert not has_unsupported_keyword(sent)
    assert "properties" in sent and "required" in sent
    assert schema.model_json_schema()["additionalProperties"] is False


async def test_generated_extra_fields_are_still_rejected(monkeypatch):
    sdk_provider(
        monkeypatch,
        {
            "reply": "Answer [1]",
            "source_ids": [1],
            "rationale": "Evidence",
            "execute_refund": True,
        },
    )
    engine = SupportEngine(
        "gemini",
        _generate_gemini_content,
        lambda: "gemini-3.8-flash",
        _gemini_http_error,
    )
    with pytest.raises(HTTPException) as raised:
        await engine.structured(Draft, "Use policy only.", {"question": "Test"})
    assert raised.value.status_code == 502
    assert "invalid structured response" in raised.value.detail


def test_nested_schemas_are_compatible_without_changing_pydantic_validation():
    class Envelope(StrictModel):
        draft: Draft

    assert not has_unsupported_keyword(gemini_response_schema(Envelope))
    original = Envelope.model_json_schema()
    assert original["additionalProperties"] is False
    assert original["$defs"]["Draft"]["additionalProperties"] is False
    with pytest.raises(ValidationError):
        Envelope.model_validate(
            {
                "draft": {
                    "reply": "Answer",
                    "rationale": "test",
                    "unexpected": "blocked",
                }
            }
        )


async def test_structured_diagnostic_exercises_the_same_sdk_schema(monkeypatch, capsys):
    provider = sdk_provider(
        monkeypatch,
        {
            "reply": "OK",
            "source_ids": [1],
            "rationale": "diagnostic",
        },
    )
    assert await check(False, True) == 0
    output = capsys.readouterr().out
    assert "ResolveAI structured generation succeeded" in output
    assert "synthetic-structured-test-key" not in output
    assert len(provider.requests) == 1
    assert "No document or customer data" in output


async def test_failed_knowledge_run_retains_safe_provider_status(
    support_context, monkeypatch
):
    from sqlalchemy import select

    from database.models import SupportRun

    monkeypatch.setenv("GEMINI_API_KEY", "synthetic-key")
    client = await support_context.account()
    response = await client.post(
        "/api/v1/workspaces",
        json={
            "name": "Structured test",
            "ai_mode": "gemini",
        },
    )
    assert response.status_code == 201, response.text
    workspace_id = response.json()["id"]
    await support_context.activate(client, workspace_id)
    failure = HTTPException(502, "Gemini rejected the generation request (HTTP 400).")

    async def fail(*args, **kwargs):
        raise failure

    monkeypatch.setattr("resolveai.router.retrieve", fail)
    response = await client.post(
        f"/api/v1/workspaces/{workspace_id}/ask",
        json={
            "question": "Order cancellation policy?",
        },
    )
    assert response.status_code == 502
    async with support_context.factory() as db:
        run = await db.scalar(select(SupportRun))
        assert run.status == "failed" and "HTTP 400" in run.error
        assert run.duration_ms is not None
        assert run.model and run.completed_at is not None
