from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from documents.services.llm import (
    LLMError,
    _parse_json_tolerant,
    answer_question,
    clean_keys,
    json_chat,
)


class ParseJsonTolerantTestCase(SimpleTestCase):
    """_parse_json_tolerant должен чинить «почти JSON» от GigaChat."""

    def test_valid_json(self):
        result = _parse_json_tolerant('{"a": 1, "b": "x"}')
        self.assertEqual(result, {"a": 1, "b": "x"})

    def test_trailing_comma(self):
        result = _parse_json_tolerant('{"a": 1, "b": 2,}')
        self.assertEqual(result, {"a": 1, "b": 2})

    def test_single_quotes(self):
        result = _parse_json_tolerant("{'a': 1}")
        self.assertEqual(result, {"a": 1})

    def test_not_json(self):
        result = _parse_json_tolerant("привет мир")
        self.assertIsNone(result)

    def test_json_array_returns_none(self):
        # Верхний уровень — массив, а не объект. Ожидаем None.
        result = _parse_json_tolerant("[1, 2, 3]")
        self.assertIsNone(result)


class CleanKeysTestCase(SimpleTestCase):
    """clean_keys убирает служебные префиксы и комментарии."""

    def test_strips_double_underscore_comments(self):
        data = {
            "title": "x",
            "__comment_title": "пояснение",
            "author": "Смирнов",
        }
        result = clean_keys(data)
        self.assertNotIn("__comment_title", result)
        self.assertEqual(result["author"], "Смирнов")

    def test_strips_single_underscore_comment(self):
        data = {"_comment_note": "x", "value": 1}
        result = clean_keys(data)
        self.assertNotIn("_comment_note", result)
        self.assertEqual(result["value"], 1)

    def test_strips_path_prefixes(self):
        data = {
            "title": "x",
            "../../../candidate_name": "Смирнов",
        }
        result = clean_keys(data)
        self.assertEqual(result["candidate_name"], "Смирнов")
        self.assertNotIn("../../../candidate_name", result)

    def test_strips_leading_underscore(self):
        data = {"_skills": ["Python"], "title": "x"}
        result = clean_keys(data)
        self.assertEqual(result["skills"], ["Python"])
        self.assertNotIn("_skills", result)

    def test_deduplicates_non_empty_wins(self):
        data = {
            "_education": "",
            "education": ["РГПУ"],
        }
        result = clean_keys(data)
        self.assertEqual(result["education"], ["РГПУ"])


class JsonChatTestCase(SimpleTestCase):
    """json_chat мокаем _get_client, чтобы не ходить в API."""

    def _mock_response(self, content: str):
        response = MagicMock()
        response.choices[0].message.content = content
        response.usage.prompt_tokens = 10
        response.usage.completion_tokens = 5
        response.usage.total_tokens = 15
        response.usage.precached_prompt_tokens = 0
        return response

    @patch("documents.services.llm._get_client")
    def test_json_chat_happy_path(self, mock_get_client):
        mock_client = MagicMock()
        mock_client.chat.return_value = self._mock_response('{"answer": "4"}')
        mock_get_client.return_value.__enter__.return_value = mock_client

        schema = {"type": "object", "properties": {"answer": {"type": "string"}}}
        parsed, usage = json_chat("system", "user", schema)

        self.assertEqual(parsed, {"answer": "4"})
        self.assertEqual(usage["prompt_tokens"], 10)
        self.assertEqual(usage["completion_tokens"], 5)

    @patch("documents.services.llm._get_client")
    def test_json_chat_repairs_broken_json(self, mock_get_client):
        mock_client = MagicMock()
        mock_client.chat.return_value = self._mock_response('{"answer": "4",}')
        mock_get_client.return_value.__enter__.return_value = mock_client

        schema = {"type": "object"}
        parsed, _ = json_chat("system", "user", schema)
        self.assertEqual(parsed, {"answer": "4"})

    def test_json_chat_empty_user(self):
        with self.assertRaises(LLMError):
            json_chat("system", "", {"type": "object"})

    @patch("documents.services.llm._get_client")
    def test_json_chat_invalid_response(self, mock_get_client):
        mock_client = MagicMock()
        mock_client.chat.return_value = self._mock_response("не json совсем")
        mock_get_client.return_value.__enter__.return_value = mock_client

        with self.assertRaises(LLMError):
            json_chat("system", "user", {"type": "object"})


class AnswerQuestionTestCase(SimpleTestCase):
    """answer_question использует json_chat. Мокаем json_chat."""

    @patch("documents.services.llm.json_chat")
    def test_answer_question_found(self, mock_json_chat):
        mock_json_chat.return_value = (
            {"found": True, "answer": "Python, Django"},
            {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        )
        result = answer_question("Какой стек?", "Резюме: Python, Django")
        self.assertTrue(result["found"])
        self.assertEqual(result["answer"], "Python, Django")

    @patch("documents.services.llm.json_chat")
    def test_answer_question_not_found(self, mock_json_chat):
        mock_json_chat.return_value = (
            {"found": False, "answer": "Нет данных"},
            {},
        )
        result = answer_question("Есть ли Kubernetes?", "Резюме: Python")
        self.assertFalse(result["found"])

    def test_answer_question_empty(self):
        with self.assertRaises(LLMError):
            answer_question("", "контекст")
        with self.assertRaises(LLMError):
            answer_question("вопрос", "")

    @patch("documents.services.llm.json_chat")
    def test_answer_question_bad_response(self, mock_json_chat):
        # Модель вернула невалидный ответ без found
        mock_json_chat.return_value = ({"answer": "x"}, {})
        with self.assertRaises(LLMError):
            answer_question("вопрос", "контекст")