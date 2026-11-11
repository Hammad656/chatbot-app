import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from google.genai import errors, types

from main import (
    DEFAULT_MODEL,
    FALLBACK_MODEL,
    _generate_gemini_content,
    _gemini_contents,
)


class GeminiContentTests(unittest.TestCase):
    def test_default_model_is_available_to_new_gemini_api_users(self):
        self.assertEqual(DEFAULT_MODEL, "gemini-3.8-flash")

    def test_converts_chat_history_and_appends_current_question(self):
        contents = _gemini_contents(
            [
                {"role": "user", "content": "What role did I have?"},
                {"role": "assistant", "content": "You were a developer."},
            ],
            "Where was it?",
        )

        self.assertEqual(
            [content.role for content in contents], ["user", "model", "user"]
        )
        self.assertEqual(
            [content.parts[0].text for content in contents],
            [
                "What role did I have?",
                "You were a developer.",
                "Where was it?",
            ],
        )


class GeminiFallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_retries_with_fallback_model_after_service_unavailable(self):
        response = SimpleNamespace(text="answer")
        generate_content = AsyncMock(
            side_effect=[
                errors.ServerError(503, {"error": {"message": "busy"}}),
                response,
            ]
        )
        client = SimpleNamespace(
            aio=SimpleNamespace(
                models=SimpleNamespace(generate_content=generate_content)
            )
        )
        contents = [types.Content(role="user", parts=[types.Part.from_text(text="hi")])]
        config = types.GenerateContentConfig()

        result = await _generate_gemini_content(
            client, DEFAULT_MODEL, contents, config
        )

        self.assertIs(result, response)
        self.assertEqual(
            [call.kwargs["model"] for call in generate_content.call_args_list],
            [DEFAULT_MODEL, FALLBACK_MODEL],
        )

    async def test_does_not_retry_non_availability_errors(self):
        error = errors.ClientError(404, {"error": {"message": "not found"}})
        generate_content = AsyncMock(side_effect=error)
        client = SimpleNamespace(
            aio=SimpleNamespace(
                models=SimpleNamespace(generate_content=generate_content)
            )
        )

        with self.assertRaises(errors.ClientError):
            await _generate_gemini_content(
                client,
                DEFAULT_MODEL,
                [],
                types.GenerateContentConfig(),
            )

        generate_content.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
