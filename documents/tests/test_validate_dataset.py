"""Проверка датасета: эталон должен подтверждаться текстом документа."""

import io
import json
import tempfile
from pathlib import Path

from django.core.management import call_command
from django.test import SimpleTestCase
from docx import Document as DocxDocument


def _make_docx(path: Path, paragraphs: list[str]) -> None:
    document = DocxDocument()
    for text in paragraphs:
        document.add_paragraph(text)
    document.save(str(path))


class ValidateDatasetTestCase(SimpleTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def _dataset(
        self,
        *,
        price: str = "89 990 руб.",
        text_price: str = "89 990 руб.",
        with_split: bool = True,
        title: str = "Ноутбук ASUS",
        text_title: str = "Спецификация: Ноутбук ASUS",
    ) -> None:
        folder = self.root / "specification"
        folder.mkdir(parents=True, exist_ok=True)

        _make_docx(
            folder / "sp_001.docx",
            [text_title, "Оперативная память: 16 ГБ", f"Цена: {text_price}"],
        )

        payload: dict = {
            "data": {
                "title": title,
                "price": price,
                "ram": "16 ГБ",
            }
        }
        if with_split:
            payload["_meta"] = {"split": "dev"}

        (folder / "sp_001.truth.json").write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )

    def _run(self, **kwargs) -> str:
        out = io.StringIO()
        call_command(
            "validate_dataset",
            dataset=str(self.root),
            stdout=out,
            **kwargs,
        )
        return out.getvalue()

    def test_matching_truth_passes(self):
        self._dataset()
        output = self._run()
        self.assertIn("с расхождениями: 0", output)

    def test_number_with_different_spacing_passes(self):
        self._dataset(price="89990", text_price="89 990 руб.")
        output = self._run()
        self.assertIn("с расхождениями: 0", output)

    def test_value_absent_from_text_is_reported(self):
        self._dataset(price="12 500 руб.")

        with self.assertRaises(SystemExit):
            self._run()

    def test_allow_misses_does_not_fail(self):
        self._dataset(price="12 500 руб.")
        output = self._run(allow_misses=True)
        self.assertIn("с расхождениями: 1", output)

    def test_missing_split_is_reported(self):
        self._dataset(with_split=False)
        output = self._run(allow_misses=True)
        self.assertIn("без поля _meta.split: 1", output)

    def test_missing_source_is_reported(self):
        self._dataset()
        (self.root / "specification" / "sp_001.docx").unlink()

        output = self._run(allow_misses=True)
        self.assertIn("без исходника: 1", output)

    def test_report_json_is_written(self):
        self._dataset(price="12 500 руб.")
        target = self.root / "report.json"

        self._run(allow_misses=True, out=str(target))

        self.assertTrue(target.exists())
        payload = json.loads(target.read_text(encoding="utf-8"))
        self.assertEqual(len(payload["mismatched"]), 1)
        self.assertIn("price", payload["fields"])

    def test_field_counter_names_problem_field(self):
        self._dataset(price="12 500 руб.")
        output = self._run(allow_misses=True)
        self.assertIn("price", output)

    def test_limit_checks_one_document_per_type(self):
        self._dataset()
        folder = self.root / "specification"
        _make_docx(folder / "sp_002.docx", ["Спецификация", "Цена: 1 руб."])
        (folder / "sp_002.truth.json").write_text(
            json.dumps(
                {"_meta": {"split": "test"}, "data": {"price": "1 руб."}},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        output = self._run(limit=1, allow_misses=True)

        self.assertIn("проверено документов: 1", output)
