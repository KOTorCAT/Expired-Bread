from django.conf import settings
from django.db import models


class DocumentQuerySet(models.QuerySet):
    """QuerySet документов с изоляцией по владельцу (этап 8).

    Все обращения из views идут через for_user, поэтому чужой документ
    невозможно получить даже по прямому id: он просто не попадёт в выборку
    и get_object_or_404 вернёт 404.
    """

    def for_user(self, user):
        """Документы пользователя. Анонимный пользователь не видит ничего."""
        if user is None or not getattr(user, "is_authenticated", False):
            return self.none()
        return self.filter(user=user)


class Document(models.Model):
    class Status(models.TextChoices):
        UPLOADED = "uploaded", "Загружен"
        PROCESSING = "processing", "Обрабатывается"
        REVIEW = "review", "Требует проверки"
        READY = "ready", "Готов"
        FAILED = "failed", "Ошибка"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="documents",
        null=True,
        blank=True,
        verbose_name="Владелец",
    )
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

    objects = DocumentQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "status"]),
            models.Index(fields=["document_type"]),
        ]

    def __str__(self) -> str:
        return self.title or self.original_filename

    @property
    def type_label(self) -> str:
        """Человекочитаемое название типа документа.

        Пустая строка, если тип не определён или его нет в реестре.
        Импорт внутри метода: модели не должны зависеть от схем на этапе
        загрузки приложения.
        """
        if not self.document_type:
            return ""
        from documents.schemas import get_label

        return get_label(self.document_type)


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
