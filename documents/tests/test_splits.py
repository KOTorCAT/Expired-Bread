"""Разбиение dev/test: одно определение для генератора и оценки."""

import json
import tempfile
from pathlib import Path

from django.test import SimpleTestCase

from documents.evaluation.runner import load_dataset
from documents.evaluation.splits import (
    DEV,
    TEST,
    assign_split,
    declared_split,
    resolve_split,
)


class AssignSplitTestCase(SimpleTestCase):
    def test_balance_keeps_ratio(self):
        parts = [assign_split(index, 25) for index in range(1, 26)]

        self.assertEqual(parts.count(DEV), 17)
        self.assertEqual(parts.count(TEST), 8)

    def test_first_part_is_dev(self):
        self.assertEqual(assign_split(1, 10), DEV)
        self.assertEqual(assign_split(7, 10), DEV)
        self.assertEqual(assign_split(8, 10), TEST)
        self.assertEqual(assign_split(10, 10), TEST)

    def test_index_must_be_inside_range(self):
        with self.assertRaises(ValueError):
            assign_split(0, 10)
        with self.assertRaises(ValueError):
            assign_split(11, 10)

    def test_ratio_must_be_sane(self):
        with self.assertRaises(ValueError):
            assign_split(1, 10, 1.5)
        with self.assertRaises(ValueError):
            assign_split(1, 10, 0)

    def test_single_document_type_goes_to_test(self):
        # int(1 * 0.7) == 0, значит единственный документ попадает в test
        self.assertEqual(assign_split(1, 1), TEST)


class DeclaredSplitTestCase(SimpleTestCase):
    def test_reads_meta(self):
        self.assertEqual(declared_split({"_meta": {"split": "dev"}}), DEV)
        self.assertEqual(declared_split({"_meta": {"split": "test"}}), TEST)

    def test_unknown_value_is_rejected(self):
        self.assertIsNone(declared_split({"_meta": {"split": "holdout"}}))

    def test_missing_meta(self):
        self.assertIsNone(declared_split({"data": {}}))
        self.assertIsNone(declared_split({"data": {}, "_meta": {}}))
        self.assertIsNone(declared_split(None))
        self.assertIsNone(declared_split("строка"))

    def test_resolve_uses_declared_value(self):
        self.assertEqual(resolve_split({"_meta": {"split": "test"}}), TEST)


class DatasetSplitFromFileTestCase(SimpleTestCase):
    """Часть датасета берётся из файла эталона, а не из хеша имени."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def _write(self, name: str, split: str | None) -> None:
        folder = self.root / "price_list"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{name}.docx").write_bytes(b"fake")

        payload: dict = {"data": {"title": "документ"}}
        if split is not None:
            payload["_meta"] = {"split": split}

        (folder / f"{name}.truth.json").write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )

    def test_file_value_defines_selection(self):
        self._write("pl_001", "test")
        self._write("pl_002", "dev")

        self.assertEqual(
            [item.doc_id for item in load_dataset(self.root, split="test")],
            ["price_list/pl_001"],
        )
        self.assertEqual(
            [item.doc_id for item in load_dataset(self.root, split="dev")],
            ["price_list/pl_002"],
        )

    def test_document_without_split_is_in_no_part(self):
        self._write("pl_001", None)

        self.assertEqual(load_dataset(self.root, split="dev"), [])
        self.assertEqual(load_dataset(self.root, split="test"), [])

    def test_document_without_split_is_still_loaded_for_all(self):
        self._write("pl_001", None)

        items = load_dataset(self.root)

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].split, "")

    def test_split_is_available_on_item(self):
        self._write("pl_001", "test")

        self.assertEqual(load_dataset(self.root)[0].split, TEST)

    def test_parts_do_not_overlap(self):
        for index in range(1, 6):
            self._write(f"pl_{index:03d}", assign_split(index, 6))

        dev = {item.doc_id for item in load_dataset(self.root, split="dev")}
        test = {item.doc_id for item in load_dataset(self.root, split="test")}
        everything = {item.doc_id for item in load_dataset(self.root)}

        self.assertEqual(dev & test, set())
        self.assertEqual(dev | test, everything)
