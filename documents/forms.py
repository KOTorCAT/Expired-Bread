import os

from django import forms

from .models import Document


ALLOWED_EXTENSIONS = {".pdf", ".docx"}
MAX_FILE_SIZE_MB = 20
MAX_FILE_SIZE_BYTES = MAX_FILE_SIZE_MB * 1024 * 1024

DOCUMENT_TYPE_CHOICES = [
    ("price_list", "Прайс-лист"),
    ("specification", "Спецификация"),
    ("technical_document", "Техническая документация"),
    ("resume", "Резюме"),
    ("other", "Другое"),
]


class DocumentUploadForm(forms.ModelForm):
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

        return uploaded


class DocumentReviewForm(forms.ModelForm):
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
            "document_type": forms.Select(choices=DOCUMENT_TYPE_CHOICES),
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