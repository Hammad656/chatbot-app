"""Gemini configuration, bounded generation and safe diagnostics."""

import json
import logging
import os
import re

import httpx
from fastapi import HTTPException, status
from google import genai
from google.genai import errors, types
from pydantic import ValidationError

from resolveai.schemas import gemini_response_schema

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "gemini-flash-latest"
FALLBACK_MODEL = "gemini-flash-lite-latest"


def configured_gemini_model() -> str:
    return (os.getenv("GEMINI_MODEL") or "").strip().removeprefix(
        "models/"
    ) or DEFAULT_MODEL


async def _gemini_fallback_model(
    client: genai.Client, requested_model: str, status_code: int
) -> str | None:
    # Only retry a model Google actually advertises for generateContent. Avoid
    # silently selecting Pro, preview, image, audio, or embedding models.
    available = set()
    models = await client.aio.models.list(config=types.ListModelsConfig(page_size=100))
    async for model in models:
        name = (model.name or "").removeprefix("models/")
        if (
            name != requested_model
            and "generateContent" in (model.supported_actions or [])
            and re.fullmatch(
                r"gemini-(?:\d+(?:\.\d+)*-)?flash(?:-lite)?(?:-\d{3})?(?:-latest)?",
                name,
            )
        ):
            available.add(name)

    preferred = (
        (FALLBACK_MODEL, DEFAULT_MODEL)
        if status_code == 503
        else (DEFAULT_MODEL, FALLBACK_MODEL)
    )
    for name in (*preferred, *sorted(available, reverse=True)):
        if name in available:
            return name
    return None


def gemini_error_message(error: errors.APIError) -> str:
    """Bounded provider diagnostic for server logs/CLI, without API keys."""
    message = error.message or "No provider explanation was supplied."
    for name in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
        key = os.getenv(name)
        if key:
            message = message.replace(key, "[redacted]")
    message = re.sub(r"AIza[0-9A-Za-z_-]{20,}", "[redacted]", message)
    return " ".join(message.split())[:1500]


def _gemini_http_error(
    error: errors.APIError, *, model: str | None = None, purpose="generation"
) -> HTTPException:
    logger.error(
        "Gemini generation failed (provider_http=%s, model=%s): %s",
        error.code,
        model or configured_gemini_model(),
        gemini_error_message(error),
    )
    if error.code == 404:
        if purpose == "embedding":
            return HTTPException(
                503,
                "The workspace's Gemini embedding model is unavailable. Check EMBEDDING_MODEL and run python check_gemini.py --embeddings. Existing workspaces keep their original embedding model.",
            )
        return HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "The configured Gemini model is unavailable for this API key. "
                "Run python check_gemini.py from backend, set GEMINI_MODEL to a "
                "listed generateContent model in backend/.env, and restart the backend."
            ),
        )
    if error.code == 429:
        return HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                "Gemini rate limit or quota exceeded. Check your Google AI Studio "
                "project's quota and billing, then retry when capacity is available."
            ),
        )
    if error.code in (401, 403) or (
        error.code == 400
        and (
            "api key" in (error.message or "").lower()
            or "API_KEY_INVALID" in str(error.details)
        )
    ):
        return HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Gemini rejected the API key or project permissions. Check "
                "GEMINI_API_KEY and your Google AI Studio project restrictions."
            ),
        )
    if error.code == 503:
        return HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Gemini is temporarily unavailable. Please try again shortly.",
        )
    if error.code == 400:
        return HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=(
                f"Gemini rejected the {purpose} request (HTTP 400). Check the configured "
                "model and structured-output settings. Run python check_gemini.py --structured "
                "inside the backend for the provider's diagnostic."
            ),
        )
    return HTTPException(
        status_code=status.HTTP_502_BAD_GATEWAY,
        detail=(
            f"Gemini could not complete the request (provider HTTP {error.code}). "
            "Check the provider diagnostic in the backend log."
        ),
    )


async def _generate_gemini_content(
    client: genai.Client,
    model: str,
    contents: list[types.Content],
    config: types.GenerateContentConfig,
) -> types.GenerateContentResponse:
    model = model.strip().removeprefix("models/") or DEFAULT_MODEL
    try:
        return await client.aio.models.generate_content(
            model=model,
            contents=contents,
            config=config,
        )
    except errors.APIError as error:
        if error.code not in (404, 503):
            raise
        fallback = await _gemini_fallback_model(client, model, error.code)
        if fallback is None:
            raise
        logger.warning(
            "Gemini model %s failed with HTTP %s; retrying once with listed model %s.",
            model,
            error.code,
            fallback,
        )
        return await client.aio.models.generate_content(
            model=fallback,
            contents=contents,
            config=config,
        )


class GeminiLLMClient:
    """Provider boundary shared by support and company-knowledge chat."""

    def __init__(
        self,
        generate=_generate_gemini_content,
        configured_model=configured_gemini_model,
        map_error=_gemini_http_error,
    ):
        self.generate = generate
        self.configured_model = configured_model
        self.map_error = map_error
        self.tokens = 0
        self.model = configured_model()

    async def complete_structured(self, schema, instruction, data):
        key = os.getenv("GEMINI_API_KEY")
        if not key:
            raise HTTPException(
                503, "Set GEMINI_API_KEY for this workspace, or use Local demo mode."
            )
        client = genai.Client(
            api_key=key, http_options=types.HttpOptions(timeout=30_000)
        )
        try:
            for attempt in range(2):
                response = await self.generate(
                    client,
                    self.configured_model(),
                    [
                        types.Content(
                            role="user",
                            parts=[
                                types.Part.from_text(
                                    text=json.dumps(data, ensure_ascii=False)
                                )
                            ],
                        )
                    ],
                    types.GenerateContentConfig(
                        system_instruction=instruction
                        + (
                            " Return only valid JSON with no additional fields; follow the response schema exactly."
                            if attempt
                            else ""
                        ),
                        response_mime_type="application/json",
                        response_schema=gemini_response_schema(schema),
                        max_output_tokens=2500,
                    ),
                )
                usage = response.usage_metadata
                self.tokens += int(getattr(usage, "total_token_count", 0) or 0)
                self.model = getattr(response, "model_version", None) or self.model
                try:
                    result = schema.model_validate_json(response.text or "{}")
                    if re.search(
                        r"AIza[0-9A-Za-z_-]{20,}|sk-[0-9A-Za-z_-]{24,}",
                        getattr(result, "reply", ""),
                    ):
                        raise ValueError("Unsafe credential-like output.")
                    return result
                except (ValidationError, ValueError):
                    if attempt:
                        raise HTTPException(
                            502,
                            "The model returned an invalid structured response after one repair attempt. Retry or ask a person.",
                        ) from None
        except errors.APIError as error:
            raise self.map_error(error) from error
        except httpx.HTTPError as error:
            raise HTTPException(
                503, "Cannot reach Gemini. Check connectivity and retry."
            ) from error
        finally:
            await client.aio.aclose()
