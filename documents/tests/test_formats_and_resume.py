"""Форматы исходников и продолжение прерванного прогона."""

import json
import tempfile
from pathlib import Path
from types import SimpleNamespace

from django.test import SimpleTestCase

from documents.evaluation.matching import FieldMatch
from documents.evaluation.metrics import Metrics
from documents.evaluation.report import render_markdown
from documents.evaluation.runner import (
    PROGRESS_FILENAME,
    append_progress,
    load_dataset,
    load_progress,
    progress_key,
    run_evaluation,
)


def _write_truth(folder: Path, name: str, split: str, data: dict) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{name}.truth.json").write_text(
        json.dumps(
            {"_meta": {"doc_type": folder.name, "split": split}, "data": data},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


class FormatsTestCase(SimpleTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

        folder = self.root / "price_list"
        _write_truth(folder, "pl_001", "dev", {"price": "10 руб."})
        (folder / "pl_001.docx").write_bytes(b"docx")
        (folder / "pl_001.pdf").write_bytes(b"pdf")

    def test_docx_only(self):
        items = load_dataset(self.root, formats=("docx",))
        self.assertEqual([item.source_format for item in items], ["docx"])

    def test_both_formats_give_two_items(self):
        items = load_dataset(self.root, formats=("docx", "pdf"))

        self.assertEqual([item.source_format for item in items], ["docx", "pdf"])
        self.assertEqual({item.doc_id for item in items}, {"price_list/pl_001"})

    def test_missing_format_is_skipped(self):
        (self.root / "price_list" / "pl_001.pdf").unlink()

        items = load_dataset(self.root, formats=("docx", "pdf"))

        self.assertEqual([item.source_format for item in items], ["docx"])

    def test_unknown_format_raises(self):
        with self.assertRaises(ValueError):
            load_dataset(self.root, formats=("xlsx",))


class MetricsByFormatTestCase(SimpleTestCase):
    def _metrics(self) -> Metrics:
        metrics = Metrics()
        metrics.add_document(
            "price_list",
            [FieldMatch(path="price", kind="money", expected="10", actual="10", status="tp")],
            source_format="docx",
        )
        metrics.add_document(
            "price_list",
            [
                FieldMatch(
                    path="price",
                    kind="money",
                    expected="10",
                    actual="",
                    status="fn",
                    category="missing",
                )
            ],
            source_format="pdf",
        )
        return metrics

    def test_formats_are_counted_separately(self):
        summary = self._metrics().to_dict()
        per_format = summary["per_format"]

        self.assertEqual(per_format["docx"]["micro"]["f1"], 1.0)
        self.assertEqual(per_format["pdf"]["micro"]["recall"], 0.0)
        self.assertEqual(per_format["pdf"]["documents"], 1)

    def test_errors_remember_format(self):
        summary = self._metrics().to_dict()
        self.assertEqual(summary["errors"][0]["format"], "pdf")

    def test_report_shows_format_table_for_two_formats(self):
        result = {"summary": self._metrics().to_dict(), "config": {}, "repeat": 1}
        markdown = render_markdown(result)

        self.assertIn("По форматам файлов", markdown)
        self.assertIn("| `pdf` |", markdown)

    def test_report_hides_format_table_for_single_format(self):
        metrics = Metrics()
        metrics.add_document(
            "price_list",
            [FieldMatch(path="price", kind="money", expected="1", actual="1", status="tp")],
            source_format="docx",
        )

        markdown = render_markdown(
            {"summary": metrics.to_dict(), "config": {}, "repeat": 1}
        )

        self.assertNotIn("По форматам файлов", markdown)


class ResumeTestCase(SimpleTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.progress = self.root / PROGRESS_FILENAME

        folder = self.root / "dataset" / "price_list"
        _write_truth(folder, "pl_001", "dev", {"price": "10 руб."})
        (folder / "pl_001.docx").write_bytes(b"docx")

        self.items = load_dataset(self.root / "dataset", formats=("docx",))
        self.calls: list[str] = []

    def _pipeline(self, **kwargs):
        self.calls.append(kwargs.get("text", ""))
        return SimpleNamespace(
            data={"price": "10 руб."},
            doc_type="price_list",
            meta={"usage": {"prompt_tokens": 100, "completion_tokens": 20}},
        )

    def _run(self, **kwargs):
        return run_evaluation(
            self.items,
            extractor=lambda path: "Цена: 10 руб.",
            pipeline_fn=self._pipeline,
            progress_path=self.progress,
            **kwargs,
        )

    def test_progress_is_written_per_document(self):
        self._run()

        records = [
            json.loads(line)
            for line in self.progress.read_text(encoding="utf-8").splitlines()
        ]
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["doc_id"], "price_list/pl_001")
        self.assertEqual(records[0]["format"], "docx")
        self.assertTrue(records[0]["matches"])

    def test_resume_does_not_call_model_again(self):
        first = self._run()
        calls_after_first = len(self.calls)

        second = self._run(resume=True)

        self.assertEqual(len(self.calls), calls_after_first)
        self.assertEqual(second["resumed"], 1)
        self.assertEqual(first["summary"]["documents"], second["summary"]["documents"])

    def test_resume_restores_metrics(self):
        self._run()
        second = self._run(resume=True)

        micro = second["summary"]["fields"]["micro"]
        self.assertEqual(micro["tp"], 1)
        self.assertEqual(second["summary"]["tokens"]["prompt"], 100)

    def test_without_resume_model_is_called_again(self):
        self._run()
        calls_after_first = len(self.calls)

        self._run()

        self.assertEqual(len(self.calls), calls_after_first + 1)

    def test_corrupted_progress_line_is_skipped(self):
        self.progress.write_text('{"key": "битая"\n', encoding="utf-8")

        records = load_progress(self.progress)

        self.assertEqual(records, {})

    def test_progress_key_includes_format_and_repeat(self):
        item = self.items[0]

        self.assertEqual(progress_key(item, 0), "price_list/pl_001|docx|0")
        self.assertEqual(progress_key(item, 2), "price_list/pl_001|docx|2")

    def test_appended_progress_accumulates(self):
        append_progress(self.progress, {"key": "a", "matches": []})
        append_progress(self.progress, {"key": "b", "matches": []})

        self.assertEqual(set(load_progress(self.progress)), {"a", "b"})
