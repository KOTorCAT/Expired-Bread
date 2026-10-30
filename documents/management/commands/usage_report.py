"""Отчёт о расходе токенов GigaChat: python manage.py usage_report.

Расход складывается в `Document.processing_meta["usage"]` конвейером:
там отдельные записи по классификации, извлечению и ремонту ответа.
Команда суммирует их по документу и по всей базе.

Цена задаётся в настройках (рубли за 1 млн токенов):

    GIGACHAT_PRICE_PER_1M_PROMPT=...
    GIGACHAT_PRICE_PER_1M_COMPLETION=...

Если цены не заданы, отчёт показывает только токены — без выдуманных рублей.

Примеры:

    python manage.py usage_report
    python manage.py usage_report --limit 50
    python manage.py usage_report --json eval/results/usage.json
"""

from __future__ import annotations

import json
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from documents.evaluation.metrics import _sum_usage
from documents.models import Document


def _price(name: str) -> float:
    """Цена за 1 млн токенов из настроек; 0.0, если не задана."""
    try:
        return float(getattr(settings, name, 0.0) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _cost(prompt: int, completion: int) -> float:
    """Стоимость в рублях; 0.0, если цены не настроены."""
    return (
        prompt / 1_000_000 * _price("GIGACHAT_PRICE_PER_1M_PROMPT")
        + completion / 1_000_000 * _price("GIGACHAT_PRICE_PER_1M_COMPLETION")
    )


class Command(BaseCommand):
    help = "Показывает расход токенов по документам и всего"

    def add_arguments(self, parser):
        parser.add_argument(
            "--limit",
            type=int,
            default=20,
            help="Сколько последних документов показать (0 — все)",
        )
        parser.add_argument(
            "--json",
            default="",
            help="Сохранить отчёт в JSON",
        )

    def handle(self, *args, **options):
        limit = options["limit"]

        rows = []
        total_prompt = 0
        total_completion = 0
        without_usage = 0

        queryset = Document.objects.order_by("-created_at")

        for document in queryset.iterator():
            meta = document.processing_meta or {}
            usage = meta.get("usage")
            prompt, completion = _sum_usage(usage)

            if not prompt and not completion:
                without_usage += 1
                continue

            total_prompt += prompt
            total_completion += completion
            rows.append({
                "id": document.pk,
                "title": document.title or document.original_filename,
                "document_type": document.document_type,
                "status": document.status,
                "prompt_tokens": prompt,
                "completion_tokens": completion,
                "total_tokens": prompt + completion,
                "cost_rub": round(_cost(prompt, completion), 4),
            })

        total_tokens = total_prompt + total_completion
        total_cost = _cost(total_prompt, total_completion)

        self.stdout.write(f"Документов в базе: {queryset.count()}")
        self.stdout.write(f"С учётом расхода: {len(rows)}")
        self.stdout.write(f"Без данных о расходе: {without_usage}")
        self.stdout.write("")
        self.stdout.write(f"Токены: вход {total_prompt}, выход {total_completion}, "
                          f"всего {total_tokens}")

        if _price("GIGACHAT_PRICE_PER_1M_PROMPT") or _price("GIGACHAT_PRICE_PER_1M_COMPLETION"):
            self.stdout.write(f"Стоимость: {total_cost:.2f} ₽")
        else:
            self.stdout.write(
                "Стоимость: не считается — цена не задана. Задайте "
                "GIGACHAT_PRICE_PER_1M_PROMPT и GIGACHAT_PRICE_PER_1M_COMPLETION"
            )

        if rows:
            self.stdout.write("")
            self.stdout.write("Последние документы:")
            for row in rows[:limit] if limit else rows:
                self.stdout.write(
                    f"  id={row['id']:<4} {row['title'][:38]:<38} "
                    f"вход {row['prompt_tokens']:>6}  выход {row['completion_tokens']:>5}"
                )

        if options["json"]:
            target = Path(options["json"])
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(
                json.dumps(
                    {
                        "documents_with_usage": len(rows),
                        "documents_without_usage": without_usage,
                        "prompt_tokens": total_prompt,
                        "completion_tokens": total_completion,
                        "total_tokens": total_tokens,
                        "cost_rub": round(total_cost, 4),
                        "per_document": rows,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            self.stdout.write("")
            self.stdout.write(self.style.SUCCESS(f"Отчёт сохранён: {target}"))
