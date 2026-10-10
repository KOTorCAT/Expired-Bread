from django.db import models


class Document(models.Model):
    class Status(models.TextChoices):
        UPLOADED = "uploaded", "Загружен"
        PROCESSING = "processing", "Обрабатывается"
        REVIEW = "review", "Требует проверки"
        READY = "ready", "Готов"
        FAILED = "failed", "Ошибка"

    file = models.FileField(upload_to="documents/%Y/%m/")
    original_filename = models.CharField(max_length=255)
    document_type = models.CharField(max_length=64, blank=True)
    title = models.CharField(max_length=255, blank=True)
    description = models.TextField(blank=True)
    data = models.JSONField(default=dict, blank=True)
    data_raw = models.JSONField(default=dict, blank=True)
    processing_meta = models.JSONField(default=dict, blank=True)
    extracted_text = models.TextField(blank=True, default="")
    search_text = models.TextField(blank=True, default="")
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.UPLOADED,
    )
    error_message = models.TextField(blank=True)
    document_date = models.DateField(null=True, blank=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return self.title or self.original_filename


class DocumentField(models.Model):
    """Нормализованное поле документа для поиска.

    Производная от Document.data. Пересобирается через index_document.
    Каждое поле — одна строка. Для скаляров item_index=None,
    для элементов списков (items[i].price) item_index=i.
    """

    document = models.ForeignKey(
        Document,
        on_delete=models.CASCADE,
        related_name="fields",
    )
    key = models.CharField(max_length=64)
    item_index = models.IntegerField(null=True, blank=True)

    value_text = models.CharField(max_length=512, blank=True)
    value_num = models.FloatField(null=True, blank=True)
    unit = models.CharField(max_length=16, blank=True)
    raw = models.CharField(max_length=512, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["key", "value_num"]),
            models.Index(fields=["key", "value_text"]),
            models.Index(fields=["document", "key"]),
        ]

    def __str__(self) -> str:
        return f"{self.document_id}:{self.key}={self.value_text}"