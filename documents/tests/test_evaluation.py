"""Тесты загрузки датасета, прогона и отчётов (этап 7).

Модель подменяется заглушкой: тесты не ходят в сеть и не требуют БД.
"""

import json
import tempfile
from pathlib import Path
from types import SimpleNamespace

from django.test import SimpleTestCase

from documents.evaluation.report import render_markdown, write_reports
from documents.evaluation.runner import (
    DEV_PERCENT,
    estimate_tokens,
    load_dataset,
    run_evaluation,
    sample_items,
    split_of,
)
from documents.services.pipeline import PipelineConfig


def _make_dataset(root: Path, docs: dict[str, list[str]]) -> None:
    """Создаёт датасет: docs = {'price_list': ['pl_001', 'pl_002']}."""
    for doc_type, names in docs.items():
        folder = root / doc_type
        folder.mkdir(parents=True, exist_ok=True)
        for name in names:
            (folder / f"{name}.docx").write_bytes(b"fake")
            (folder / f"{name}.truth.json").write_text(
                json.dumps({"data": {"title": f"{doc_type} {name}"}}),
                encoding="utf-8",
            )


class DatasetLoadingTestCase(SimpleTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        _make_dataset(self.root, {
            "price_list": ["pl_001", "pl_002"],
            "specification": ["sp_001"],
        })

    def test_loads_all_documents(self):
        items = load_dataset(self.root)
        self.assertEqual(len(items), 3)
        self.assertEqual({i.doc_type for i in items}, {"price_list", "specification"})

    def test_finds_docx_source(self):
        items = load_dataset(self.root)
        for item in items:
            self.assertEqual(item.source_path.suffix, ".docx")

    def test_flattens_truth_data_wrapper(self):
        items = load_dataset(self.root)
        self.assertIn("title", items[0].truth)

    def test_filter_by_type(self):
        items = load_dataset(self.root, doc_types=["price_list"])
        self.assertEqual(len(items), 2)

    def test_missing_dataset_raises(self):
        with self.assertRaises(FileNotFoundError):
            load_dataset(self.root / "nope")

    def test_unknown_split_raises(self):
        with self.assertRaises(ValueError):
            load_dataset(self.root, split="whatever")

    def test_split_is_deterministic_and_exhaustive(self):
        items = load_dataset(self.root)
        dev = {i.doc_id for i in load_dataset(self.root, split="dev")}
        test = {i.doc_id for i in load_dataset(self.root, split="test")}
        self.assertEqual(dev | test, {i.doc_id for i in items})
        self.assertEqual(dev & test, set())
        # Повторный вызов даёт тот же результат
        self.assertEqual(dev, {i.doc_id for i in load_dataset(self.root, split="dev")})

    def test_split_of_is_stable(self):
        self.assertEqual(split_of("price_list/pl_001"), split_of("price_list/pl_001"))
        self.assertIn(split_of("price_list/pl_001"), ("dev", "test"))

    def test_dev_percent_is_sane(self):
        self.assertTrue(0 < DEV_PERCENT < 100)

    def test_document_without_source_is_skipped(self):
        (self.root / "price_list" / "orphan.truth.json").write_text(
            json.dumps({"data": {"title": "x"}}), encoding="utf-8"
        )
        items = load_dataset(self.root)
        self.assertNotIn("price_list/orphan", {i.doc_id for i in items})

    def test_broken_truth_is_skipped(self):
        (self.root / "price_list" / "broken.docx").write_bytes(b"fake")
        (self.root / "price_list" / "broken.truth.json").write_text(
            "{не json", encoding="utf-8"
        )
        items = load_dataset(self.root)
        self.assertNotIn("price_list/broken", {i.doc_id for i in items})


class SampleItemsTestCase(SimpleTestCase):
    def test_stratified_sample_covers_all_types(self):
        items = [
            SimpleNamespace(doc_type="price_list", doc_id=f"p{i}") for i in range(6)
        ] + [
            SimpleNamespace(doc_type="specification", doc_id=f"s{i}") for i in range(6)
        ]
        picked = sample_items(items, 4)
        self.assertEqual(len(picked), 4)
        self.assertEqual({i.doc_type for i in picked}, {"price_list", "specification"})

    def test_larger_than_dataset_returns_everything(self):
        items = [SimpleNamespace(doc_type="price_list", doc_id="p0")]
        self.assertEqual(sample_items(items, 10), items)


class RunEvaluationTestCase(SimpleTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        _make_dataset(self.root, {"price_list": ["pl_001", "pl_002"]})
        self.items = load_dataset(self.root)

    def _stub_pipeline(self, data=None, doc_type="price_list"):
        calls = []

        def run(text, config):
            calls.append({"text": text, "config": config})
            return SimpleNamespace(
                doc_type=doc_type,
                data=data if data is not None else {"title": "price_list pl_001"},
                data_raw={},
                warnings=[],
                meta={"usage": {"extract": {"prompt_tokens": 10, "completion_tokens": 5}}},
            )

        run.calls = calls
        return run

    def test_perfect_run_gives_f1_one(self):
        truth_title = self.items[0].truth["title"]
        result = run_evaluation(
            [self.items[0]],
            extractor=lambda path: "текст документа",
            pipeline_fn=self._stub_pipeline(data={"title": truth_title}),
        )
        self.assertEqual(result["summary"]["fields"]["micro"]["f1"], 1.0)
        self.assertEqual(result["summary"]["documents"], 1)

    def test_extractor_receives_source_path(self):
        received = []
        result = run_evaluation(
            [self.items[0]],
            extractor=lambda path: received.append(path) or "текст",
            pipeline_fn=self._stub_pipeline(),
        )
        self.assertEqual(received[0], self.items[0].source_path)
        self.assertEqual(result["processed"], 1)

    def test_pipeline_failure_is_recorded(self):
        def boom(text, config):
            raise RuntimeError("модель недоступна")

        result = run_evaluation(
            [self.items[0]],
            extractor=lambda path: "текст",
            pipeline_fn=boom,
        )
        self.assertEqual(result["summary"]["failed"], 1)
        self.assertEqual(result["summary"]["documents"], 0)

    def test_extraction_failure_is_recorded(self):
        from documents.services.extractors import ExtractionError

        def failing_extract(path):
            raise ExtractionError("скан без текста")

        result = run_evaluation(
            [self.items[0]],
            extractor=failing_extract,
            pipeline_fn=self._stub_pipeline(),
        )
        self.assertEqual(result["summary"]["failed"], 1)

    def test_repeat_multiplies_runs(self):
        result = run_evaluation(
            [self.items[0]],
            repeat=3,
            extractor=lambda path: "текст",
            pipeline_fn=self._stub_pipeline(),
        )
        self.assertEqual(result["summary"]["runs"], 3)

    def test_use_truth_type_sets_doc_type_hint(self):
        stub = self._stub_pipeline()
        run_evaluation(
            [self.items[0]],
            PipelineConfig(),
            use_truth_type=True,
            extractor=lambda path: "текст",
            pipeline_fn=stub,
        )
        self.assertEqual(stub.calls[0]["config"].doc_type_hint, "price_list")

    def test_without_truth_type_hint_is_empty(self):
        stub = self._stub_pipeline()
        run_evaluation(
            [self.items[0]],
            PipelineConfig(),
            extractor=lambda path: "текст",
            pipeline_fn=stub,
        )
        self.assertEqual(stub.calls[0]["config"].doc_type_hint, "")

    def test_dry_run_does_not_call_pipeline(self):
        stub = self._stub_pipeline()
        result = run_evaluation(
            self.items,
            dry_run=True,
            extractor=lambda path: "текст " * 100,
            pipeline_fn=stub,
        )
        self.assertEqual(stub.calls, [])
        self.assertTrue(result["dry_run"])
        self.assertGreater(result["estimated_tokens"], 0)

    def test_max_tokens_stops_early(self):
        stub = self._stub_pipeline()
        result = run_evaluation(
            self.items * 5,
            dry_run=True,
            max_tokens=1,
            extractor=lambda path: "текст " * 100,
            pipeline_fn=stub,
        )
        self.assertTrue(result["stopped_early"])

    def test_estimate_tokens_grows_with_text(self):
        self.assertGreater(estimate_tokens("x" * 4000), estimate_tokens("x" * 40))


class ReportTestCase(SimpleTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        _make_dataset(self.root, {"specification": ["sp_001"]})
        items = load_dataset(self.root)

        def run(text, config):
            return SimpleNamespace(
                doc_type="specification",
                data={"title": "не то, что в эталоне"},
                data_raw={},
                warnings=[],
                meta={"usage": {}},
            )

        self.result = run_evaluation(
            items,
            extractor=lambda path: "текст",
            pipeline_fn=run,
        )

    def test_writes_four_files(self):
        paths = write_reports(self.result, self.root / "out")
        self.assertEqual(
            set(paths), {"summary", "per_field", "errors", "report"}
        )
        for path in paths.values():
            self.assertTrue(path.exists(), msg=str(path))

    def test_summary_json_is_valid(self):
        paths = write_reports(self.result, self.root / "out")
        payload = json.loads(paths["summary"].read_text(encoding="utf-8"))
        self.assertIn("summary", payload)
        self.assertIn("errors", payload["summary"])

    def test_errors_csv_has_header_and_rows(self):
        paths = write_reports(self.result, self.root / "out")
        lines = paths["errors"].read_text(encoding="utf-8-sig").splitlines()
        self.assertGreaterEqual(len(lines), 2)
        self.assertIn("category", lines[0])

    def test_markdown_contains_key_sections(self):
        markdown = render_markdown(self.result)
        self.assertIn("# Оценка качества", markdown)
        self.assertIn("Precision (micro)", markdown)
        self.assertIn("## По полям", markdown)
