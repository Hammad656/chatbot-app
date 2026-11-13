"""Gemini embeddings; vectors stay in the application's PostgreSQL database."""

import math
import os

from fastapi import HTTPException
from google import genai
from google.genai import errors, types

from ai.gemini import _gemini_http_error

EMBEDDING_DIMENSION = 768


async def embed_texts(
    texts: list[str], model: str, *, query: bool = False
) -> list[list[float]]:
    if not texts:
        return []
    key = os.getenv("GEMINI_API_KEY")
    if not key:
        raise HTTPException(
            503,
            "Set GEMINI_API_KEY to embed company documents, or use Local demo mode.",
        )
    client = genai.Client(api_key=key, http_options=types.HttpOptions(timeout=30_000))
    vectors = []
    try:
        for start in range(0, len(texts), 64):
            batch = texts[start : start + 64]
            response = await client.aio.models.embed_content(
                model=model,
                contents=batch,
                config=types.EmbedContentConfig(
                    output_dimensionality=EMBEDDING_DIMENSION,
                    task_type="RETRIEVAL_QUERY" if query else "RETRIEVAL_DOCUMENT",
                ),
            )
            if len(response.embeddings or []) != len(batch):
                raise ValueError(
                    "The embedding provider returned an unexpected number of vectors."
                )
            for embedding in response.embeddings:
                values = embedding.values or []
                if len(values) != EMBEDDING_DIMENSION or not all(
                    math.isfinite(x) for x in values
                ):
                    raise ValueError(
                        "Embedding dimension does not match the workspace's 768-dimensional index."
                    )
                norm = math.sqrt(sum(x * x for x in values))
                if norm == 0:
                    raise ValueError("The embedding provider returned an empty vector.")
                vectors.append([x / norm for x in values])
    except errors.APIError as error:
        raise _gemini_http_error(error, model=model, purpose="embedding") from error
    finally:
        await client.aio.aclose()
    return vectors
