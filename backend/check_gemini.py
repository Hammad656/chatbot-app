"""Diagnose Gemini model access using backend/.env without printing credentials."""

import argparse
import asyncio
import os
from pathlib import Path

import httpx
from dotenv import load_dotenv
from google import genai
from google.genai import errors, types

from main import _gemini_http_error, _generate_gemini_content, configured_gemini_model


async def check(test_generation: bool) -> int:
    load_dotenv(Path(__file__).with_name(".env"))
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        print("GEMINI_API_KEY is missing. Set it securely in backend/.env.")
        return 1

    configured = configured_gemini_model()
    print(f"Configured model: {configured}")
    client = genai.Client(
        api_key=api_key,
        http_options=types.HttpOptions(timeout=45_000),
    )
    try:
        models = await client.aio.models.list(
            config=types.ListModelsConfig(page_size=100)
        )
        available = []
        async for model in models:
            if model.name and "generateContent" in (model.supported_actions or []):
                available.append(model.name.removeprefix("models/"))
        print("Models advertised for generateContent:")
        for name in sorted(available):
            print(f"  {name}")
        if not available:
            print("No generation models were advertised for this API key.")
            return 1
        print("Model listing succeeded. Generation still depends on model access and quota.")
        if test_generation:
            response = await _generate_gemini_content(
                client,
                configured,
                [types.Content(role="user", parts=[types.Part.from_text(text="Reply only with OK.")])],
                types.GenerateContentConfig(max_output_tokens=512),
            )
            if not (response.text or "").strip():
                print("Generation returned no text. Check the model's response/token settings.")
                return 1
            print("Generation succeeded. No CV content was used in this check.")
        return 0
    except errors.APIError as error:
        print(f"Google returned HTTP {error.code}: {_gemini_http_error(error).detail}")
        return 1
    except httpx.HTTPError:
        print("Cannot reach Gemini. Check network connectivity and retry.")
        return 1
    finally:
        await client.aio.aclose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--test",
        action="store_true",
        help="Also send a short generation request (uses the project's Gemini quota).",
    )
    args = parser.parse_args()
    raise SystemExit(asyncio.run(check(args.test)))
