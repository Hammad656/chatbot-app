import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from google.genai import errors, types

from ai.gemini import (
    DEFAULT_MODEL,
    FALLBACK_MODEL,
    _gemini_http_error,
    _generate_gemini_content,
    configured_gemini_model,
)


class ModelPage:
    """SDK model listing is awaited first, then iterated asynchronously."""

    def __init__(self, models):
        self.models = models

    async def __aiter__(self):
        for model in self.models:
            yield model


def generation_model(name):
    return types.Model(name=f"models/{name}", supported_actions=["generateContent"])


def client_with_models(generate_content, available=()):
    return SimpleNamespace(
        aio=SimpleNamespace(
            models=SimpleNamespace(
                generate_content=generate_content,
                list=AsyncMock(return_value=ModelPage(available)),
            ),
            aclose=AsyncMock(),
        )
    )


class GeminiContentTests(unittest.TestCase):
    def test_normalizes_configured_model_and_defaults_blank_values(self):
        for value in ["", "   "]:
            with (
                self.subTest(value=value),
                patch.dict(os.environ, {"GEMINI_MODEL": value}),
            ):
                self.assertEqual(configured_gemini_model(), DEFAULT_MODEL)
        with patch.dict(os.environ, {"GEMINI_MODEL": " models/gemini-1.5-flash "}):
            self.assertEqual(configured_gemini_model(), "gemini-1.5-flash")


class GeminiFallbackTests(unittest.IsolatedAsyncioTestCase):
    async def generate(self, client, model=DEFAULT_MODEL):
        return await _generate_gemini_content(
            client,
            model,
            [
                types.Content(
                    role="user", parts=[types.Part.from_text(text="Company policy?")]
                )
            ],
            types.GenerateContentConfig(
                system_instruction="Use only the company documents."
            ),
        )

    async def test_healthy_request_never_lists_models_or_retries(self):
        response = SimpleNamespace(text="answer")
        client = client_with_models(AsyncMock(return_value=response))
        self.assertIs(await self.generate(client), response)
        client.aio.models.generate_content.assert_awaited_once()
        client.aio.models.list.assert_not_awaited()

    async def test_stale_15_model_404_recovers_using_advertised_model(self):
        response = SimpleNamespace(text="The policy allows returns.")
        generate = AsyncMock(
            side_effect=[
                errors.ClientError(
                    404,
                    {
                        "error": {
                            "message": "models/gemini-1.5-flash is not found for API version v1beta"
                        }
                    },
                ),
                response,
            ]
        )
        client = client_with_models(generate, [generation_model(DEFAULT_MODEL)])
        with patch.dict(os.environ, {"GEMINI_MODEL": "gemini-1.5-flash"}):
            result = await self.generate(client, configured_gemini_model())
        self.assertIs(result, response)
        calls = generate.call_args_list
        self.assertEqual(
            [call.kwargs["model"] for call in calls],
            ["gemini-1.5-flash", DEFAULT_MODEL],
        )
        self.assertIs(calls[0].kwargs["contents"], calls[1].kwargs["contents"])
        self.assertIs(calls[0].kwargs["config"], calls[1].kwargs["config"])
        client.aio.models.list.assert_awaited_once()

    async def test_503_prefers_advertised_flash_lite_fallback(self):
        response = SimpleNamespace(text="answer")
        generate = AsyncMock(
            side_effect=[
                errors.ServerError(503, {"error": {"message": "busy"}}),
                response,
            ]
        )
        client = client_with_models(generate, [generation_model(FALLBACK_MODEL)])
        self.assertIs(await self.generate(client), response)
        self.assertEqual(
            [c.kwargs["model"] for c in generate.call_args_list],
            [DEFAULT_MODEL, FALLBACK_MODEL],
        )

    async def test_can_use_versioned_model_when_alias_is_not_listed(self):
        response = SimpleNamespace(text="answer")
        client = client_with_models(
            AsyncMock(
                side_effect=[
                    errors.ClientError(404, {"error": {"message": "not found"}}),
                    response,
                ]
            ),
            [generation_model("gemini-2.5-flash")],
        )
        self.assertIs(await self.generate(client), response)
        self.assertEqual(
            client.aio.models.generate_content.call_args.kwargs["model"],
            "gemini-2.5-flash",
        )

    async def test_never_guesses_fallback_or_uses_pro_preview_audio_image_models(self):
        original = errors.ClientError(404, {"error": {"message": "not found"}})
        available = [
            generation_model(name)
            for name in [
                DEFAULT_MODEL,
                "gemini-2.5-pro",
                "gemini-2.5-flash-preview",
                "gemini-2.5-flash-image",
                "gemini-2.5-flash-tts",
            ]
        ]
        available.append(
            types.Model(
                name="models/gemini-2.5-flash", supported_actions=["countTokens"]
            )
        )
        client = client_with_models(AsyncMock(side_effect=original), available)
        with self.assertRaises(errors.ClientError) as raised:
            await self.generate(client)
        self.assertIs(raised.exception, original)
        client.aio.models.generate_content.assert_awaited_once()

    async def test_normalizes_models_prefix_before_generation(self):
        client = client_with_models(
            AsyncMock(return_value=SimpleNamespace(text="answer"))
        )
        await self.generate(client, f" models/{DEFAULT_MODEL} ")
        self.assertEqual(
            client.aio.models.generate_content.call_args.kwargs["model"], DEFAULT_MODEL
        )

    async def test_quota_key_and_invalid_request_errors_do_not_trigger_model_switch(
        self,
    ):
        for code in [400, 401, 403, 429, 500]:
            with self.subTest(code=code):
                error = errors.APIError(
                    code, {"error": {"message": "provider failure"}}
                )
                client = client_with_models(AsyncMock(side_effect=error))
                with self.assertRaises(errors.APIError):
                    await self.generate(client)
                client.aio.models.generate_content.assert_awaited_once()
                client.aio.models.list.assert_not_awaited()

    async def test_failed_fallback_stops_after_two_generation_attempts(self):
        failure = errors.ServerError(503, {"error": {"message": "busy"}})
        client = client_with_models(
            AsyncMock(side_effect=failure), [generation_model(FALLBACK_MODEL)]
        )
        with self.assertRaises(errors.ServerError):
            await self.generate(client)
        self.assertEqual(client.aio.models.generate_content.await_count, 2)
        client.aio.models.list.assert_awaited_once()

    async def test_listing_failure_does_not_make_an_unverified_generation_request(self):
        client = client_with_models(
            AsyncMock(
                side_effect=errors.ClientError(404, {"error": {"message": "not found"}})
            )
        )
        client.aio.models.list.side_effect = errors.ClientError(
            403, {"error": {"message": "forbidden"}}
        )
        with self.assertRaises(errors.ClientError) as raised:
            await self.generate(client)
        self.assertEqual(raised.exception.code, 403)
        client.aio.models.generate_content.assert_awaited_once()


class GeminiErrorTests(unittest.TestCase):
    def test_actionable_errors_do_not_expose_raw_provider_messages(self):
        for provider_code, expected_http, text in [
            (404, 503, "GEMINI_MODEL"),
            (429, 429, "quota"),
            (401, 503, "API key"),
            (403, 503, "API key"),
            (503, 503, "temporarily unavailable"),
            (500, 502, "Gemini could not complete"),
        ]:
            with self.subTest(code=provider_code):
                error = errors.APIError(
                    provider_code, {"error": {"message": "private-provider-detail"}}
                )
                result = _gemini_http_error(error)
                self.assertEqual(result.status_code, expected_http)
                self.assertIn(text, result.detail)
                self.assertNotIn("private-provider-detail", result.detail)

    def test_google_invalid_key_400_is_identified(self):
        error = errors.ClientError(
            400,
            {"error": {"message": "API key not valid. Please pass a valid API key."}},
        )
        result = _gemini_http_error(error)
        self.assertEqual(result.status_code, 503)
        self.assertIn("GEMINI_API_KEY", result.detail)

    def test_invalid_request_has_safe_public_detail_and_redacted_provider_log(self):
        key = "AIza" + "a" * 35
        error = errors.ClientError(
            400,
            {
                "error": {
                    "message": (
                        "Invalid JSON payload: unknown schema property.\nrequest_key: "
                        + key
                    )
                }
            },
        )
        with (
            patch.dict(os.environ, {"GEMINI_API_KEY": key}),
            self.assertLogs("ai.gemini", level="ERROR") as logs,
        ):
            result = _gemini_http_error(error)
        output = " ".join(logs.output)
        self.assertEqual(result.status_code, 502)
        self.assertIn("HTTP 400", result.detail)
        self.assertIn("--structured", result.detail)
        self.assertIn("unknown schema property", output)
        self.assertIn("provider_http=400", output)
        self.assertIn("[redacted]", output)
        self.assertNotIn(key, output)
        self.assertNotIn("unknown schema property", result.detail)
        self.assertNotIn(key, result.detail)


if __name__ == "__main__":
    unittest.main()
