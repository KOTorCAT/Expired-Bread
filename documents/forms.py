"""Формы приложения.

Список типов документов берётся из реестра схем (documents.schemas),
а не дублируется здесь — иначе он расходился бы с поиском и промптами.
"""

import json
import os

from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User
from pydantic import ValidationError as PydanticValidationError

from documents.schemas import TYPE_LABELS, get_schema
from documents.services.normalization import parse_date

from .models import Document


ALLOWED_EXTENSIONS = {".pdf", ".docx"}
MAX_FILE_SIZE_MB = 20
MAX_FILE_SIZE_BYTES = MAX_FILE_SIZE_MB * 1024 * 1024

# Значение «тип не выбран» — тогда работает классификация моделью.
AUTO_TYPE_VALUE = ""

DOCUMENT_TYPE_CHOICES = [
    (doc_type, label) for doc_type, label in TYPE_LABELS.items()
]


def _format_schema_errors(exc: PydanticValidationError) -> str:
    """Короткое человекочитаемое сообщение об ошибке схемы."""
    errors = exc.errors()
    parts = []
    for error in errors[:5]:
        location = ".".join(str(item) for item in error.get("loc", ())) or "данные"
        parts.append(f"{location}: {error.get('msg', 'ошибка')}")
    if len(errors) > 5:
        parts.append(f"…ещё {len(errors) - 5}")
    return "; ".join(parts)


class DocumentUploadForm(forms.ModelForm):
    doc_type_hint = forms.ChoiceField(
        required=False,
        choices=[(AUTO_TYPE_VALUE, "Определить автоматически")] + DOCUMENT_TYPE_CHOICES,
        label="Тип документа",
        help_text=(
            "Если выбрать тип, система не будет тратить вызов модели "
            "на классификацию."
        ),
    )

    class Meta:
        model = Document
        fields = ["file"]
        widgets = {
            "file": forms.ClearableFileInput(attrs={"accept": ".pdf,.docx"}),
        }

    def clean_file(self):
        uploaded = self.cleaned_data["file"]

        ext = os.path.splitext(uploaded.name)[1].lower()
        if ext not in ALLOWED_EXTENSIONS:
            raise forms.ValidationError(
                f"Недопустимый формат файла. "
                f"Разрешены: {', '.join(sorted(ALLOWED_EXTENSIONS))}."
            )

        if uploaded.size > MAX_FILE_SIZE_BYTES:
            raise forms.ValidationError(
                f"Файл слишком большой. Максимум — {MAX_FILE_SIZE_MB} МБ."
            )

        # Проверка сигнатуры файла: расширение легко подделать.
        head = uploaded.read(8)
        uploaded.seek(0)
        if not self._signature_matches(ext, head):
            raise forms.ValidationError(
                "Содержимое файла не похоже на выбранный формат."
            )

        return uploaded

    @staticmethod
    def _signature_matches(ext: str, head: bytes) -> bool:
        if ext == ".pdf":
            return head.startswith(b"%PDF-")
        # DOCX — это ZIP-архив
        return head.startswith(b"PK\x03\x04")


class DocumentReviewForm(forms.ModelForm):
    """Проверка и правка данных, извлечённых моделью.

    Простые поля правятся обычными inputs, списки и словари —
    JSON-текстом. Перед сохранением JSON валидируется схемой типа,
    поэтому в базу не попадёт структура, с которой не умеет работать
    поиск.
    """

    data_json = forms.CharField(
        required=False,
        label="Данные документа (JSON)",
        widget=forms.Textarea(
            attrs={"rows": 18, "class": "json-editor", "spellcheck": "false"}
        ),
        help_text=(
            "Значения копируйте как в документе, с единицами и валютой. "
            "Списки позиций и характеристики правятся прямо в JSON."
        ),
    )

    class Meta:
        model = Document
        fields = ["title", "document_type", "description", "document_date"]
        labels = {
            "title": "Название",
            "document_type": "Тип документа",
            "description": "Описание",
            "document_date": "Дата документа",
        }
        widgets = {
            "title": forms.TextInput(attrs={"class": "text-input"}),
            "document_type": forms.Select(
                choices=DOCUMENT_TYPE_CHOICES,
                attrs={"class": "text-input"},
            ),
            "description": forms.Textarea(attrs={"rows": 4, "class": "text-input"}),
            "document_date": forms.DateInput(
                attrs={"type": "date", "class": "text-input"},
                format="%Y-%m-%d",
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Принимаем оба формата: ISO и российский
        self.fields["document_date"].input_formats = [
            "%Y-%m-%d",
            "%d.%m.%Y",
        ]
        # <input type="date"> понимает только ISO. Если отдать ему
        # локализованное «15.03.2025», браузер очистит поле, и при
        # сохранении дата документа потеряется.
        self.fields["document_date"].widget.format = "%Y-%m-%d"
        if self.instance is not None and self.instance.pk and not self.is_bound:
            announced = self.instance.document_date
            if announced is None:
                # Прежняя версия формы теряла дату при сохранении —
                # восстанавливаем её из уже извлечённых данных.
                announced = parse_date((self.instance.data or {}).get("date"))
            if announced is not None:
                self.initial["document_date"] = announced
            self.initial["data_json"] = json.dumps(
                self.instance.data or {},
                ensure_ascii=False,
                indent=2,
            )

    def clean_data_json(self):
        raw = (self.cleaned_data.get("data_json") or "").strip()
        if not raw:
            # Пустое поле — не трогаем данные, которые уже в документе
            return None
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise forms.ValidationError(
                f"Некорректный JSON: {exc.msg} "
                f"(строка {exc.lineno}, колонка {exc.colno})."
            )
        if not isinstance(parsed, dict):
            raise forms.ValidationError(
                "Ожидается JSON-объект вида { ... }, "
                "а не список или строка."
            )
        return parsed

    def clean(self):
        cleaned = super().clean()
        data = cleaned.get("data_json")
        if data is None:
            return cleaned

        doc_type = cleaned.get("document_type") or self.instance.document_type
        schema = get_schema(doc_type)

        try:
            validated = schema.model_validate(data)
        except PydanticValidationError as exc:
            label = TYPE_LABELS.get(doc_type, doc_type)
            self.add_error(
                "data_json",
                forms.ValidationError(
                    f"Данные не соответствуют схеме «{label}»: "
                    f"{_format_schema_errors(exc)}"
                ),
            )
        else:
            cleaned["data"] = validated.model_dump()

        return cleaned


class RegistrationForm(UserCreationForm):
    """Простая регистрация без подтверждения почты."""

    email = forms.EmailField(required=False, label="Email")

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("username", "email")
