from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from documents.services.llm import (
    LLMError,
    analyze_document,
    validate_document_data,
)


class ValidateDocumentDataTestCase(SimpleTestCase):
    def test_valid_data(self):
        data = {"document_type": "resume", "title": "Резюме"}
        self.assertEqual(validate_document_data(data), data)

    def test_missing_document_type(self):
        with self.assertRaises(LLMError):
            validate_document_data({"title": "Резюме"})

    def test_missing_title(self):
        with self.assertRaises(LLMError):
            validate_document_data({"document_type": "resume"})

    def test_wrong_type(self):
        with self.assertRaises(LLMError):
            validate_document_data({"document_type": 123, "title": "x"})


class AnalyzeDocumentTestCase(SimpleTestCase):
    def _mock_response(self, content: str):
        response = MagicMock()
        response.choices[0].message.content = content
        return response

    @patch("documents.services.llm._get_client")
    def test_analyze_document_happy_path(self, mock_get_client):
        mock_client = MagicMock()
        mock_client.chat.return_value = self._mock_response(
            '{"document_type": "resume", "title": "Резюме", "description": "Тест"}'
        )
        mock_get_client.return_value.__enter__.return_value = mock_client

        result = analyze_document("какой-то текст")
        self.assertEqual(result["document_type"], "resume")
        self.assertEqual(result["title"], "Резюме")

    @patch("documents.services.llm._get_client")
    def test_analyze_document_invalid_json(self, mock_get_client):
        mock_client = MagicMock()
        mock_client.chat.return_value = self._mock_response("не json")
        mock_get_client.return_value.__enter__.return_value = mock_client

        with self.assertRaises(LLMError):
            analyze_document("текст")

    def test_analyze_document_empty_text(self):
        with self.assertRaises(LLMError):
            analyze_document("")

    def test_strips_service_fields(self):
        """Служебные поля (__comment_*, _comment_*) удаляются."""
        data = {
            "document_type": "report",
            "title": "Отчёт",
            "__comment_title": "пояснение",
            "_comment_note": "ещё одно",
            "author": "Смирнов",
        }
        result = validate_document_data(data)
        self.assertNotIn("__comment_title", result)
        self.assertNotIn("_comment_note", result)
        self.assertEqual(result["author"], "Смирнов")

    def test_strips_path_prefixes(self):
        """Префиксы ../../ в ключах снимаются."""
        data = {
            "document_type": "resume",
            "title": "Резюме",
            "../../../candidate_name": "Смирнов",
            "../../skills": ["Python"],
        }
        result = validate_document_data(data)
        self.assertEqual(result["candidate_name"], "Смирнов")
        self.assertEqual(result["skills"], ["Python"])
        self.assertNotIn("../../../candidate_name", result)

    def test_strips_leading_underscore(self):
        """Одиночное _ в начале ключа снимается."""
        data = {
            "document_type": "resume",
            "title": "Резюме",
            "_candidate_name": "Смирнов",
            "_skills": ["Python"],
        }
        result = validate_document_data(data)
        self.assertEqual(result["candidate_name"], "Смирнов")
        self.assertEqual(result["skills"], ["Python"])
        self.assertNotIn("_candidate_name", result)

    def test_deduplicates_after_cleaning(self):
        """При коллизии ключей после очистки непустое значение побеждает."""
        data = {
            "document_type": "resume",
            "title": "Резюме",
            "_education": "",
            "education": ["РГПУ им. Герцена"],
            "_telegram": "@user",
            "_": "",
            "-_": "",
        }
        result = validate_document_data(data)
        self.assertEqual(result["education"], ["РГПУ им. Герцена"])
        self.assertEqual(result["telegram"], "@user")
        self.assertNotIn("_", result)
        self.assertNotIn("-_", result)