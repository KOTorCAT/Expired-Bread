"""Тесты накопления метрик (этап 7)."""

from django.test import SimpleTestCase

from documents.evaluation.matching import compare_data
from documents.evaluation.metrics import Counter, Metrics, field_name


class FieldNameTestCase(SimpleTestCase):
    def test_strips_list_indices(self):
        self.assertEqual(field_name("items[2].price"), "items.price")
        self.assertEqual(field_name("requirements[0]"), "requirements")
        self.assertEqual(field_name("price"), "price")


class CounterTestCase(SimpleTestCase):
    def test_precision_recall_f1(self):
        counter = Counter(tp=8, fp=2, fn=4)
        self.assertEqual(counter.precision, 0.8)
        self.assertEqual(counter.recall, round(8 / 12, 4))
        self.assertEqual(counter.support, 12)

    def test_zero_division_is_safe(self):
        counter = Counter()
        self.assertEqual(counter.precision, 0.0)
        self.assertEqual(counter.recall, 0.0)
        self.assertEqual(counter.f1, 0.0)


class MetricsTestCase(SimpleTestCase):
    def _add(self, metrics, expected, actual, doc_type="specification", **kwargs):
        matches = compare_data(doc_type, expected, actual)
        metrics.add_document(doc_type, matches, **kwargs)
        return matches

    def test_perfect_run(self):
        metrics = Metrics()
        self._add(metrics, {"title": "Док", "price": "100 руб."},
                  {"title": "Док", "price": "100"})
        summary = metrics.to_dict()
        self.assertEqual(summary["fields"]["micro"]["tp"], 2)
        self.assertEqual(summary["fields"]["micro"]["fp"], 0)
        self.assertEqual(summary["fields"]["micro"]["fn"], 0)
        self.assertEqual(summary["fields"]["micro"]["f1"], 1.0)

    def test_wrong_value_counts_as_fp_and_fn(self):
        metrics = Metrics()
        self._add(metrics, {"price": "100 руб."}, {"price": "999 руб."})
        micro = metrics.to_dict()["fields"]["micro"]
        self.assertEqual(micro["tp"], 0)
        self.assertEqual(micro["fp"], 1)
        self.assertEqual(micro["fn"], 1)

    def test_missing_and_hallucination(self):
        metrics = Metrics()
        self._add(
            metrics,
            {"price": "100 руб."},
            {"manufacturer": "Acer"},
        )
        micro = metrics.to_dict()["fields"]["micro"]
        self.assertEqual(micro["fp"], 1)   # manufacturer выдуман
        self.assertEqual(micro["fn"], 1)   # price не извлечён

    def test_errors_are_collected(self):
        metrics = Metrics()
        self._add(metrics, {"price": "100 руб."}, {})
        summary = metrics.to_dict()
        self.assertEqual(len(summary["errors"]), 1)
        self.assertEqual(summary["errors"][0]["path"], "price")
        self.assertEqual(summary["errors"][0]["category"], "missing")

    def test_per_field_grouping_flattens_indices(self):
        metrics = Metrics()
        self._add(
            metrics,
            {"items": [{"price": "1"}, {"price": "2"}]},
            {"items": [{"price": "1"}, {"price": "999"}]},
            doc_type="price_list",
        )
        per_field = metrics.to_dict()["fields"]["per_field"]
        self.assertIn("items.price", per_field)
        self.assertEqual(per_field["items.price"]["tp"], 1)
        self.assertEqual(per_field["items.price"]["fn"], 1)

    def test_classification_accuracy(self):
        metrics = Metrics()
        self._add(metrics, {"title": "x"}, {"title": "x"},
                  expected_type="price_list", actual_type="price_list")
        self._add(metrics, {"title": "y"}, {"title": "y"},
                  expected_type="price_list", actual_type="specification")
        classification = metrics.to_dict()["classification"]
        self.assertEqual(classification["total"], 2)
        self.assertEqual(classification["correct"], 1)
        self.assertEqual(classification["accuracy"], 0.5)
        self.assertEqual(classification["confusion"]["price_list"]["specification"], 1)

    def test_format_errors_are_counted_separately(self):
        metrics = Metrics()
        self._add(metrics, {"price": "по запросу"}, {"price": "по запросу"})
        micro = metrics.to_dict()["fields"]["micro"]
        self.assertEqual(micro["format_errors"], 1)
        self.assertEqual(micro["tp"], 0)

    def test_failed_documents_are_tracked(self):
        metrics = Metrics()
        metrics.add_failure("price_list", RuntimeError("модель недоступна"))
        summary = metrics.to_dict()
        self.assertEqual(summary["failed"], 1)
        self.assertEqual(summary["documents"], 0)
        self.assertIn("модель недоступна", summary["failures"][0]["error"])

    def test_usage_tokens_are_summed(self):
        metrics = Metrics()
        metrics.add_document(
            "specification",
            [],
            usage={
                "classify": {"prompt_tokens": 100, "completion_tokens": 5},
                "extract": {"prompt_tokens": 1000, "completion_tokens": 200},
            },
        )
        tokens = metrics.to_dict()["tokens"]
        self.assertEqual(tokens["prompt"], 1100)
        self.assertEqual(tokens["completion"], 205)
        self.assertEqual(tokens["total"], 1305)

    def test_macro_f1_averages_fields_equally(self):
        metrics = Metrics()
        # Одно поле идеально, другое полностью провалено
        metrics.add_document(
            "specification",
            compare_data("specification", {"title": "A", "price": "1"},
                         {"title": "A"}),
        )
        summary = metrics.to_dict()
        # title: f1 = 1.0; price: tp=0, fn=1 → f1 = 0.0
        self.assertEqual(summary["fields"]["macro_f1"], 0.5)
