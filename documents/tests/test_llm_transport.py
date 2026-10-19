"""Тесты транспорта GigaChat: таймаут, ретраи и понятные сообщения.

Проверяется то, из-за чего падала обработка резюме: таймаут в 30 секунд
по умолчанию у SDK и лимит частоты 429, который попадал в интерфейс
сырым дампом заголовков httpx.
"""

import os
from unittest.mock import patch

import httpx
from django.test import SimpleTestCase
from gigachat.exceptions import AuthenticationError, RateLimitError

from documents.services import llm
from documents.services.llm import (
    DEFAULT_TIMEOUT,
    humanize_llm_error,
    is_retryable_error,
)


class _FakeRateLimitError(RateLimitError):
    """RateLimitError без реального HTTP-ответа.

    У настоящего класса retry_after — read-only property, поэтому
    перекрываем его собственной реализацией.
    """

    def __init__(self, retry_after: int = 30) -> None:
        self._retry_after = retry_after

    @property
    def retry_after(self) -> int:  # type: ignore[override]
        return self._retry_after


def _rate_limit(retry_after: int = 30) -> RateLimitError:
    """Собирает RateLimitError без реального HTTP-ответа."""
    return _FakeRateLimitError(retry_after)


def _auth_error() -> AuthenticationError:
    return AuthenticationError.__new__(AuthenticationError)


class HumanizeTestCase(SimpleTestCase):
    def test_rate_limit_explains_and_suggests_wait(self):
        text = humanize_llm_error(_rate_limit(60))
        self.assertIn("частоту", text)
        self.assertIn("429", text)
        self.assertIn("60", text)

    def test_read_timeout_explained(self):
        text = humanize_llm_error(httpx.ReadTimeout("The read operation timed out"))
        self.assertIn("не ответил", text)

    def test_plain_timeout_text_is_recognised(self):
        # Ровно этот текст попадал в error_message документа
        text = humanize_llm_error(RuntimeError("The read operation timed out"))
        self.assertIn("не ответил", text)

    def test_auth_error_explained(self):
        text = humanize_llm_error(_auth_error())
        self.assertIn("авторизацию", text)

    def test_raw_headers_are_not_leaked(self):
        exc = RuntimeError(
            "429 https://api.giga.chat/v1/chat/completions: "
            "b'{\"status\":429,\"message\":\"Too Many Requests\"}'; "
            "Headers({'server': 'nginx', 'set-cookie': 'secret-token'})"
        )
        text = humanize_llm_error(exc)
        self.assertNotIn("set-cookie", text)
        self.assertNotIn("Headers", text)
        self.assertNotIn("secret-token", text)

    def test_long_error_is_truncated(self):
        text = humanize_llm_error(RuntimeError("x" * 1000))
        self.assertLessEqual(len(text), 300)

    def test_none_gives_generic_message(self):
        self.assertIn("GigaChat", humanize_llm_error(None))


class RetryPolicyTestCase(SimpleTestCase):
    def test_rate_limit_is_retryable(self):
        self.assertTrue(is_retryable_error(_rate_limit()))

    def test_timeout_is_retryable(self):
        self.assertTrue(is_retryable_error(httpx.ReadTimeout("t")))

    def test_connection_error_is_retryable(self):
        self.assertTrue(is_retryable_error(httpx.ConnectError("нет сети")))

    def test_auth_error_is_not_retryable(self):
        self.assertFalse(is_retryable_error(_auth_error()))


class ClientSettingsTestCase(SimpleTestCase):
    def test_client_gets_timeout_and_retries(self):
        with patch.dict(os.environ, {"GIGACHAT_CREDENTIALS": "key"}), patch.object(
            llm, "GigaChat"
        ) as client:
            llm._get_client()

        kwargs = client.call_args.kwargs
        self.assertEqual(kwargs["timeout"], DEFAULT_TIMEOUT)
        self.assertGreaterEqual(kwargs["max_retries"], 1)
        self.assertEqual(kwargs["retry_on_status_codes"], llm.RETRY_ON_STATUS_CODES)
        self.assertIn(429, kwargs["retry_on_status_codes"])

    def test_timeout_is_taken_from_env(self):
        with patch.dict(os.environ, {"GIGACHAT_TIMEOUT": "45"}):
            self.assertEqual(llm._timeout_seconds(), 45.0)

    def test_bad_timeout_falls_back_to_default(self):
        with patch.dict(os.environ, {"GIGACHAT_TIMEOUT": "не число"}):
            self.assertEqual(llm._timeout_seconds(), DEFAULT_TIMEOUT)

    def test_zero_retries_are_allowed(self):
        with patch.dict(os.environ, {"GIGACHAT_MAX_RETRIES": "0"}):
            self.assertEqual(llm._max_retries(), 0)

    def test_missing_credentials_raise_llm_error(self):
        env = {k: v for k, v in os.environ.items() if k != "GIGACHAT_CREDENTIALS"}
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaises(llm.LLMError):
                llm._get_client()
