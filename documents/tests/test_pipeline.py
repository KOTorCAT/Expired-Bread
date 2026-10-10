from unittest.mock import patch

from django.test import SimpleTestCase

from documents.schemas.price_list import PriceListData
from documents.services.pipeline import (
    PipelineConfig,
    classify,
    extract,
    run_pipeline,
    slim_schema,
    validate_with_repair,
)


class SlimSchemaTestCase(SimpleTestCase):
    """slim_schema удаляет служебные title из JSON Schema."""

    def test_removes_string_title(self):
        node = {
            "type": "object",
            "title": "SomeName",       # ← удалить
            "properties": {
                "name": {
                    "type": "string",
                    "title": "Name",   # ← удалить
                },
            },
        }
        result = slim_schema(node)
        self.assertNotIn("title", result)
        self.assertNotIn("title", result["properties"]["name"])

    def test_keeps_non_string_title(self):
        # title — поле данных с типом, а не метаданные
        node = {
            "properties": {
                "title": {"type": "string"},  # ← оставить
            },
        }
        result = slim_schema(node)
        self.assertIn("title", result["properties"])


class ClassifyTestCase(SimpleTestCase):
    """classify вызывает json_chat, если doc_type_hint не задан."""

    @patch("documents.services.pipeline.json_chat")
    def test_classify_from_hint(self, mock_json_chat):
        config = PipelineConfig(doc_type_hint="price_list")
        doc_type, usage = classify("любой текст", config)
        self.assertEqual(doc_type, "price_list")
        self.assertTrue(usage.get("from_hint"))
        mock_json_chat.assert_not_called()

    @patch("documents.services.pipeline.json_chat")
    def test_classify_via_model(self, mock_json_chat):
        mock_json_chat.return_value = ({"document_type": "specification"}, {})
        config = PipelineConfig()
        doc_type, _ = classify("текст", config)
        self.assertEqual(doc_type, "specification")

    @patch("documents.services.pipeline.json_chat")
    def test_classify_unknown_type(self, mock_json_chat):
        mock_json_chat.return_value = ({"document_type": "неизвестный"}, {})
        config = PipelineConfig()
        doc_type, _ = classify("текст", config)
        self.assertEqual(doc_type, "other")


class ExtractTestCase(SimpleTestCase):
    """extract вызывает json_chat со slim-схемой."""

    @patch("documents.services.pipeline.json_chat")
    def test_extract_passes_slim_schema(self, mock_json_chat):
        mock_json_chat.return_value = ({"title": "x"}, {})
        config = PipelineConfig()
        data, _ = extract("текст", PriceListData, config)
        self.assertEqual(data, {"title": "x"})

        # Проверим, что title удалены из json_schema
        call_kwargs = mock_json_chat.call_args.kwargs
        schema = call_kwargs["json_schema"]
        self.assertNotIn("title", schema.get("properties", {}).get("name", {}))


class ValidateWithRepairTestCase(SimpleTestCase):
    """validate_with_repair: happy path, ремонт, двойная ошибка."""

    def test_valid_data_no_repair(self):
        raw = {
            "title": "Прайс-лист",
            "items": [{"name": "Товар", "price": "100"}],
        }
        data, warnings, usage = validate_with_repair(
            raw, PriceListData, "текст", PipelineConfig(),
        )
        self.assertEqual(data["title"], "Прайс-лист")
        self.assertEqual(warnings, [])
        self.assertEqual(usage, {})

    @patch("documents.services.pipeline.json_chat")
    def test_repair_after_validation_error(self, mock_json_chat):
        # Первый raw — невалиден (нет title, price=None)
        raw = {"items": [{"name": "Товар", "price": None}]}
        # Ремонт возвращает валидные данные
        mock_json_chat.return_value = (
            {"title": "Прайс-лист", "items": [{"name": "Товар", "price": "100"}]},
            {"prompt_tokens": 5},
        )
        data, warnings, usage = validate_with_repair(
            raw, PriceListData, "текст", PipelineConfig(),
        )
        self.assertIn("repaired", warnings)
        self.assertEqual(data["title"], "Прайс-лист")

    @patch("documents.services.pipeline.json_chat")
    def test_repair_twice_fails(self, mock_json_chat):
        from documents.services.llm import LLMError

        # Первый raw — невалиден, ремонт тоже невалиден
        raw = {"items": []}
        mock_json_chat.return_value = ({"items": []}, {})

        with self.assertRaises(LLMError):
            validate_with_repair(raw, PriceListData, "текст", PipelineConfig())


class RunPipelineTestCase(SimpleTestCase):
    """run_pipeline — интеграция всех шагов."""

    @patch("documents.services.pipeline.extract")
    @patch("documents.services.pipeline.classify")
    def test_run_pipeline_happy_path(self, mock_classify, mock_extract):
        mock_classify.return_value = ("price_list", {"prompt_tokens": 100})
        mock_extract.return_value = (
            {
                "title": "Прайс-лист",
                "items": [{"name": "Товар", "price": "100"}],
            },
            {"prompt_tokens": 200},
        )

        result = run_pipeline("длинный текст " * 20)

        self.assertEqual(result.doc_type, "price_list")
        self.assertEqual(result.data["title"], "Прайс-лист")
        self.assertEqual(result.warnings, [])
        self.assertIn("usage", result.meta)

    @patch("documents.services.pipeline.classify")
    def test_run_pipeline_short_text(self, mock_classify):
        from documents.services.llm import LLMError

        # Текст короче 30 символов и нет file_path
        with self.assertRaises(LLMError):
            run_pipeline("коротко")