"""Команда оценки качества: python manage.py evaluate.

Примеры:

    # быстрый прогон на dev-части с двумя повторами
    python manage.py evaluate --split dev --repeat 2

    # сколько это будет стоить, без вызовов модели
    python manage.py evaluate --split dev --sample 6 --dry-run

    # сравнение двух конфигураций
    python manage.py evaluate --config eval/configs/max.json --out eval/results/max
    python manage.py evaluate --config eval/configs/lite.json --out eval/results/lite
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from documents.evaluation.report import write_reports
from documents.evaluation.runner import (
    PROGRESS_FILENAME,
    SUPPORTED_FORMATS,
    SUPPORTED_SPLITS,
    load_dataset,
    run_evaluation,
    sample_items,
)
from documents.services.pipeline import PipelineConfig

# Поля PipelineConfig, которые можно задать в JSON-конфиге.
NUMERIC_CONFIG_FIELDS = {"classify_chars", "max_chars"}
BOOL_CONFIG_FIELDS = {"use_cache", "use_vision"}
STRING_CONFIG_FIELDS = {"model", "classify_model", "prompt_version", "doc_type_hint"}


class Command(BaseCommand):
    help = "Оценивает качество извлечения полей на датасете eval/dataset"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dataset",
            default=str(Path(settings.BASE_DIR) / "eval" / "dataset"),
            help="Каталог датасета (по умолчанию eval/dataset)",
        )
        parser.add_argument(
            "--split",
            default="all",
            choices=SUPPORTED_SPLITS,
            help="Часть датасета: dev, test или all",
        )
        parser.add_argument(
            "--format",
            default="docx",
            choices=("docx", "pdf", "both"),
            help=(
                "Какие исходники оценивать: docx (по умолчанию), pdf "
                "или both — два прогона одного документа для сравнения форматов"
            ),
        )
        parser.add_argument(
            "--resume",
            action="store_true",
            help="Продолжить прерванный прогон из <out>/progress.jsonl",
        )
        parser.add_argument(
            "--config",
            default="",
            help="JSON-файл с настройками конвейера",
        )
        parser.add_argument(
            "--out",
            default="",
            help="Каталог для отчётов (по умолчанию eval/results/<дата>)",
        )
        parser.add_argument(
            "--repeat",
            type=int,
            default=1,
            help="Сколько раз обработать каждый документ",
        )
        parser.add_argument(
            "--sample",
            type=int,
            default=0,
            help="Стратифицированная выборка N документов (0 — все)",
        )
        parser.add_argument(
            "--types",
            default="",
            help="Список типов через запятую (по умолчанию все)",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=0,
            help="Ограничить число документов без стратификации",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Только оценить расход токенов, модель не вызывать",
        )
        parser.add_argument(
            "--max-tokens",
            type=int,
            default=0,
            help="Предохранитель: остановиться при превышении бюджета",
        )
        parser.add_argument(
            "--use-truth-type",
            action="store_true",
            help="Брать тип из эталона вместо классификации (режим «тип из формы»)",
        )

    def handle(self, *args, **options):
        config_path = options["config"]
        config, options_extra = self._load_config(config_path)

        use_truth_type = options["use_truth_type"] or options_extra.get(
            "use_truth_type", False
        )

        doc_types = [
            value.strip()
            for value in options["types"].split(",")
            if value.strip()
        ] or None

        formats = (
            SUPPORTED_FORMATS
            if options["format"] == "both"
            else (options["format"],)
        )

        items = load_dataset(
            options["dataset"],
            split=options["split"],
            doc_types=doc_types,
            formats=formats,
        )

        if not items:
            raise CommandError(
                f"В датасете {options['dataset']} нет документов "
                f"для split={options['split']}. "
                f"Сначала сгенерируйте датасет: "
                f"python manage.py make_dataset --per-type 25"
            )

        original_count = len(items)

        if options["limit"] > 0:
            items = items[: options["limit"]]
        if options["sample"] > 0:
            items = sample_items(items, options["sample"])

        self.stdout.write(
            f"Документов в выборке: {len(items)} из {original_count} "
            f"(split={options['split']}, repeat={options['repeat']}, "
            f"форматы: {', '.join(formats)})"
        )

        if options["dry_run"]:
            self.stdout.write("Режим dry-run: модель вызываться не будет")

        out_dir = options["out"] or self._default_out_dir()
        progress_path = None

        if options["resume"]:
            if not options["out"]:
                raise CommandError(
                    "--resume требует --out: укажите папку прогона, "
                    "в ней ищется progress.jsonl"
                )
            progress_path = Path(out_dir) / PROGRESS_FILENAME
            if progress_path.exists():
                self.stdout.write(f"Продолжаю прогон: {progress_path}")
            else:
                self.stdout.write(
                    self.style.WARNING(
                        "Файла прогресса нет — прогон начнётся с начала"
                    )
                )

        result = run_evaluation(
            items,
            config,
            repeat=options["repeat"],
            dry_run=options["dry_run"],
            max_tokens=options["max_tokens"],
            use_truth_type=use_truth_type,
            progress_path=progress_path,
            resume=options["resume"] and not options["dry_run"],
        )

        paths = write_reports(result, out_dir)

        self._print_summary(result)

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("Отчёты:"))
        for name, path in paths.items():
            self.stdout.write(f"  {name}: {path}")

    # --- Вспомогательное ----------------------------------------------------

    def _default_out_dir(self) -> str:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        return str(Path(settings.BASE_DIR) / "eval" / "results" / stamp)

    def _load_config(self, config_path: str) -> tuple[PipelineConfig, dict]:
        if not config_path:
            return PipelineConfig(), {}

        path = Path(config_path)
        if not path.exists():
            raise CommandError(f"Файл конфигурации не найден: {path}")

        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise CommandError(f"Некорректный JSON в {path}: {exc}") from exc

        if not isinstance(payload, dict):
            raise CommandError("Конфигурация должна быть JSON-объектом")

        known = NUMERIC_CONFIG_FIELDS | BOOL_CONFIG_FIELDS | STRING_CONFIG_FIELDS

        kwargs = {}
        remaining = {}
        for key, value in payload.items():
            if key in known:
                kwargs[key] = value
            else:
                remaining[key] = value

        try:
            config = PipelineConfig(**kwargs)
        except TypeError as exc:
            raise CommandError(f"Неверные параметры конфигурации: {exc}") from exc

        if not config.use_cache:
            self.stdout.write(
                self.style.WARNING(
                    "Кэш отключён — каждый повтор пойдёт в модель. "
                    "Для экономии включите use_cache в конфиге."
                )
            )

        return config, remaining

    def _print_summary(self, result: dict) -> None:
        summary = result["summary"]
        micro = summary["fields"]["micro"]

        if result.get("dry_run"):
            self.stdout.write(
                f"Ожидаемый расход: ≈ {result.get('estimated_tokens', 0)} токенов "
                f"на {result.get('processed', 0)} документов"
            )
            return

        self.stdout.write("")
        self.stdout.write(f"Обработано документов: {summary['documents']}")
        if result.get("resumed"):
            self.stdout.write(
                f"  из них взято из прогресса: {result['resumed']}"
            )
        self.stdout.write(f"Ошибок обработки: {summary['failed']}")
        self.stdout.write(
            f"Precision: {micro['precision']:.3f}  "
            f"Recall: {micro['recall']:.3f}  "
            f"F1: {micro['f1']:.3f}  "
            f"(macro F1: {summary['fields']['macro_f1']:.3f})"
        )

        classification = summary["classification"]
        if classification["total"]:
            self.stdout.write(
                f"Классификация: {classification['accuracy']:.3f} "
                f"({classification['correct']}/{classification['total']})"
            )

        if micro["format_errors"]:
            self.stdout.write(
                self.style.WARNING(
                    f"Ошибок нормализации: {micro['format_errors']} "
                    f"(правьте parse_number / parse_quantity, а не промпт)"
                )
            )

        if result.get("stopped_early"):
            self.stdout.write(
                self.style.WARNING("Прогон остановлен по лимиту токенов")
            )
