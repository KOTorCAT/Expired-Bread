import hashlib
import mimetypes
from pathlib import Path
from urllib.parse import quote

from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from .forms import (
    DOCUMENT_TYPE_CHOICES,
    DocumentReviewForm,
    DocumentUploadForm,
    RegistrationForm,
    format_schema_errors,
)
from pydantic import ValidationError as PydanticValidationError

from .models import Document, DocumentField
from .presenters import (
    FILTER_FIELDS,
    label_for,
    OPERATORS,
    build_editor,
    build_filter_rows,
    build_view,
    describe_filter,
    parse_editor,
)
from .schemas import get_schema
from .services.background import recover_stuck, start_processing
from .services.document_service import (
    confirm_document,
    delete_document,
)
from .services.pipeline import PipelineConfig
from .services.qa_service import ask
from .services.search_service import (
    filters_from_rows,
    parse_filter_expression,
    parse_filters,
    search_documents,
)

PAGE_SIZE = 20


# История вопросов хранится в сессии: переживает переходы по страницам,
# но не базу — для демонстрации этого достаточно.
QA_HISTORY_KEY = "qa_history"
QA_HISTORY_LIMIT = 10


def _page_query(request) -> str:
    """Параметры запроса без номера страницы — для ссылок пагинации."""
    params = request.GET.copy()
    params.pop("page", None)
    return params.urlencode()


def _attach_matches(documents, filters) -> None:
    """Подписывает документы значениями полей, попавших в фильтр.

    Без этого непонятно, почему документ нашёлся: видно название,
    но не видно, какое условие сработало.
    """
    if not filters or not documents:
        return

    keys = sorted({item.key for item in filters})
    grouped: dict[int, dict[str, str]] = {}

    for row in DocumentField.objects.filter(document__in=documents, key__in=keys):
        value = row.raw or row.value_text
        if value:
            grouped.setdefault(row.document_id, {}).setdefault(row.key, value)

    for document in documents:
        values = grouped.get(document.pk, {})
        document.match_labels = [
            f"{label_for(document.document_type, key)}: {value}"
            for key, value in values.items()
        ][:3]


def _file_digest(uploaded) -> str:
    """SHA-256 содержимого файла: одинаковые файлы дают одинаковый хеш."""
    digest = hashlib.sha256()
    for chunk in uploaded.chunks():
        digest.update(chunk)
    uploaded.seek(0)
    return digest.hexdigest()

# Иконка для плитки статуса на главной (имена из SVG-спрайта в base.html).
STATUS_ICONS = {
    "uploaded": "upload",
    "processing": "clock",
    "review": "edit",
    "ready": "check",
    "failed": "alert",
}


# --- Вспомогательное --------------------------------------------------------

def _owned(request, pk: int) -> Document:
    """Документ текущего пользователя или 404.

    Единственная точка получения документа во всех view: чужой или
    несуществующий id неотличимы друг от друга.
    """
    return get_object_or_404(Document.objects.for_user(request.user), pk=pk)


def _page(request, queryset, size: int = PAGE_SIZE):
    paginator = Paginator(queryset, size)
    page = paginator.get_page(request.GET.get("page"))
    return page, paginator


# --- Страницы ---------------------------------------------------------------

@login_required
def index(request):
    """Главная: краткая сводка по документам пользователя."""
    queryset = Document.objects.for_user(request.user)

    counts = {choice.value: 0 for choice in Document.Status}
    for row in queryset.values("status").annotate(total=Count("id")):
        counts[row["status"]] = row["total"]

    status_rows = [
        (value, label, counts[value], STATUS_ICONS.get(value, "file"))
        for value, label in Document.Status.choices
    ]

    return render(
        request,
        "documents/index.html",
        {
            "total": queryset.count(),
            "status_rows": status_rows,
            "recent": queryset[:6],
        },
    )


@login_required
def document_list(request):
    status = request.GET.get("status", "").strip()
    document_type = request.GET.get("type", "").strip()

    queryset = Document.objects.for_user(request.user)

    if status in {choice.value for choice in Document.Status}:
        queryset = queryset.filter(status=status)
    if document_type:
        queryset = queryset.filter(document_type=document_type)

    page, paginator = _page(request, queryset)

    return render(
        request,
        "documents/document_list.html",
        {
            "documents": page.object_list,
            "page_obj": page,
            "paginator": paginator,
            "is_paginated": page.has_other_pages(),
            "page_query": _page_query(request),
            "total": paginator.count,
            "status": status,
            "document_type": document_type,
            "status_choices": Document.Status.choices,
            "type_choices": DOCUMENT_TYPE_CHOICES,
        },
    )


@login_required
def document_upload(request):
    if request.method == "POST":
        form = DocumentUploadForm(request.POST, request.FILES)
        if form.is_valid():
            uploaded = request.FILES["file"]
            digest = _file_digest(uploaded)

            # Тот же файл у того же пользователя — не создаём копию,
            # открываем уже загруженный. Повтор возможен с ?force=1.
            duplicate = None
            if not request.POST.get("force"):
                duplicate = (
                    Document.objects.for_user(request.user)
                    .filter(content_hash=digest)
                    .exclude(content_hash="")
                    .first()
                )

            if duplicate is not None:
                messages.warning(
                    request,
                    f"Этот файл уже загружен: "
                    f"«{duplicate.title or duplicate.original_filename}». "
                    f"Открыт существующий документ.",
                )
                return redirect("documents:detail", pk=duplicate.pk)

            document = form.save(commit=False)
            document.user = request.user
            document.original_filename = uploaded.name
            document.content_hash = digest
            document.status = Document.Status.UPLOADED
            document.save()

            hint = (form.cleaned_data.get("doc_type_hint") or "").strip()
            config = PipelineConfig(doc_type_hint=hint) if hint else None

            # Обработка уходит в фоновый поток: страница отвечает сразу,
            # а статус на странице документа обновляется опросом.
            start_processing(document.pk, config=config)
            messages.info(
                request,
                "Документ принят. Обработка идёт в фоне — статус обновится сам.",
            )
            return redirect("documents:detail", pk=document.pk)
    else:
        form = DocumentUploadForm()

    return render(
        request,
        "documents/document_upload.html",
        {"form": form},
    )


@login_required
def document_detail(request, pk):
    document = _owned(request, pk)

    # Если обработка числится идущей слишком долго (процесс перезапускали),
    # статус не должен остаться «Обрабатывается» навсегда.
    if recover_stuck(document):
        document.refresh_from_db()

    meta = document.processing_meta or {}
    suffix = Path(document.original_filename or document.file.name or "").suffix.lower()

    # Размер файла: FileField.size может бросить, если файл удалён с диска.
    try:
        file_size = document.file.size if document.file else None
    except OSError:
        file_size = None

    file_kind = {".pdf": "PDF", ".docx": "DOCX"}.get(suffix, "Файл")

    return render(
        request,
        "documents/document_detail.html",
        {
            "document": document,
            "warnings": meta.get("warnings", []),
            "corrected_fields": meta.get("corrected_fields", []),
            "is_pdf": suffix == ".pdf",
            "file_kind": file_kind,
            "data_view": build_view(document.document_type, document.data),
            "file_size": file_size,
            # Время старта обработки в секундах: по нему JS считает счётчик.
            "started_at": int(document.updated_at.timestamp()),
            "is_processing": document.status in (
                Document.Status.UPLOADED,
                Document.Status.PROCESSING,
            ),
        },
    )


@login_required
def document_review(request, pk):
    document = _owned(request, pk)

    # Форму можно открыть только в review или ready.
    # В других статусах редактировать нечего или рано.
    if document.status not in (Document.Status.REVIEW, Document.Status.READY):
        messages.warning(
            request,
            "Этот документ нельзя редактировать в текущем статусе.",
        )
        return redirect("documents:detail", pk=document.pk)

    # Тип до валидации: form.is_valid() перезаписывает поля экземпляра
    # модели, поэтому сравнивать с ним после проверки уже нельзя.
    original_type = document.document_type

    # Что показать в полях: сохранённые данные или то, что пользователь
    # только что ввёл, чтобы правки не пропадали при ошибке валидации.
    editor_source = document.data

    if request.method == "POST":
        form = DocumentReviewForm(request.POST, instance=document)

        if form.is_valid():
            chosen_type = form.cleaned_data.get("document_type") or document.document_type

            if chosen_type != original_type:
                # Схема сменилась: поля перерисуем на следующем шаге,
                # иначе пользователь правил бы поля чужого типа.
                document.document_type = chosen_type
                document.save(update_fields=["document_type", "updated_at"])
                messages.info(
                    request,
                    "Тип документа изменён. Проверьте поля и сохраните ещё раз.",
                )
                return redirect("documents:review", pk=document.pk)

            payload = parse_editor(request.POST, chosen_type)

            # Название и описание правятся полями документа, но в данных
            # они тоже должны совпадать — иначе поиск и отчёт разойдутся.
            payload["title"] = form.cleaned_data.get("title") or ""
            payload["description"] = form.cleaned_data.get("description") or ""

            # Дата правится одним полем-календарём; в данных она хранится
            # строкой, поэтому переносим её туда же.
            chosen_date = form.cleaned_data.get("document_date")
            payload["date"] = chosen_date.isoformat() if chosen_date else ""

            try:
                validated = get_schema(chosen_type).model_validate(payload)
            except PydanticValidationError as exc:
                messages.error(
                    request,
                    "Данные не сохранены: " + format_schema_errors(exc),
                )
                editor_source = payload
            else:
                cleaned = dict(form.cleaned_data)
                cleaned["data"] = validated.model_dump()
                confirm_document(document.id, cleaned, user=request.user)
                messages.success(
                    request,
                    "Данные сохранены, документ доступен в поиске.",
                )
                return redirect("documents:detail", pk=document.pk)
    else:
        form = DocumentReviewForm(instance=document)

    meta = document.processing_meta or {}

    return render(
        request,
        "documents/document_review.html",
        {
            "document": document,
            "form": form,
            "warnings": meta.get("warnings", []),
            "editor": build_editor(document.document_type, editor_source),
        },
    )


@login_required
@require_POST
def document_reprocess(request, pk):
    """Повторный запуск обработки. Только POST."""
    document = _owned(request, pk)
    if start_processing(document.pk):
        messages.info(request, "Документ отправлен на повторную обработку.")
    else:
        messages.warning(request, "Документ уже обрабатывается.")
    return redirect("documents:detail", pk=document.pk)


@login_required
def document_download(request, pk):
    """Отдача оригинала с проверкой владельца.

    Прямые ссылки на /media/ убраны: статическая раздача файлов в обход
    проверки прав — это утечка чужих документов.
    """
    document = _owned(request, pk)

    if not document.file:
        messages.error(request, "У документа нет файла.")
        return redirect("documents:detail", pk=document.pk)

    filename = document.original_filename or Path(document.file.name).name

    try:
        handle = document.file.open("rb")
    except FileNotFoundError:
        messages.error(request, "Файл не найден на диске.")
        return redirect("documents:detail", pk=document.pk)

    return FileResponse(handle, as_attachment=True, filename=filename)


@login_required
@require_POST
def document_delete(request, pk):
    """Удаление документа вместе с файлом. Только POST."""
    document = _owned(request, pk)
    name = str(document)
    delete_document(document)
    messages.success(request, f"Документ «{name}» удалён.")
    return redirect("documents:list")


@login_required
def document_search(request):
    query = request.GET.get("q", "").strip()
    status = request.GET.get("status", "").strip()
    document_type = request.GET.get("type", "").strip()

    # Понятный конструктор: поле + условие + значение.
    filters = filters_from_rows(request.GET)

    # Старый синтаксис ?f=ключ:оператор:значение остаётся для совместимости.
    if not filters:
        filters = parse_filters(request.GET)

    text_query = query
    if query and not filters:
        inline_filters = parse_filter_expression(query)
        if inline_filters:
            filters = inline_filters
            text_query = ""

    queryset = search_documents(
        user=request.user,
        query=text_query,
        status=status,
        document_type=document_type,
        filters=filters,
        only_ready=True,
    )

    page, paginator = _page(request, queryset)

    # Ссылки страниц должны сохранять и запрос, и условия.
    page_query = _page_query(request)
    _attach_matches(page.object_list, filters)

    return render(
        request,
        "documents/search_results.html",
        {
            "documents": page.object_list,
            "page_obj": page,
            "paginator": paginator,
            "is_paginated": page.has_other_pages(),
            "page_query": page_query,
            "total": paginator.count,
            "query": query,
            "status": status,
            "document_type": document_type,
            "type_choices": DOCUMENT_TYPE_CHOICES,
            "filter_rows": build_filter_rows(filters),
            "filter_fields": FILTER_FIELDS,
            "operators": OPERATORS,
            "chips": [describe_filter(item) for item in filters],
            "has_filters": bool(filters),
        },
    )


@login_required
def document_ask(request):
    if request.GET.get("clear"):
        request.session.pop(QA_HISTORY_KEY, None)
        messages.info(request, "История вопросов очищена.")
        return redirect("documents:ask")

    if request.method == "POST":
        question = request.POST.get("question", "").strip()
        if question:
            result = ask(question, user=request.user)

            history = request.session.get(QA_HISTORY_KEY, [])
            history.insert(0, {
                "question": question,
                "answer": result["answer"],
                "found": result["found"],
                "sources": [
                    {
                        "id": document.pk,
                        "title": document.title or document.original_filename,
                        "status": document.status,
                        "status_label": document.get_status_display(),
                    }
                    for document in result["sources"]
                ],
            })
            request.session[QA_HISTORY_KEY] = history[:QA_HISTORY_LIMIT]

        # Переадресация после POST: обновление страницы не задаст
        # тот же вопрос второй раз.
        return redirect("documents:ask")

    return render(
        request,
        "documents/ask.html",
        {
            "history": request.session.get(QA_HISTORY_KEY, []),
        },
    )


CONTENT_TYPES = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


@login_required
def document_status(request, pk):
    """Статус обработки для опроса со страницы документа."""
    document = _owned(request, pk)
    recover_stuck(document)

    return JsonResponse({
        "id": document.pk,
        "status": document.status,
        "status_label": document.get_status_display(),
        "error": document.error_message,
        "has_data": bool(document.data),
        "finished": document.status in (
            Document.Status.REVIEW,
            Document.Status.READY,
            Document.Status.FAILED,
        ),
    })


@login_required
def document_preview(request, pk):
    """Отдаёт оригинал для встроенного просмотра."""
    document = _owned(request, pk)

    if not document.file:
        raise Http404("У документа нет файла")

    suffix = Path(document.file.name).suffix.lower()
    content_type = (
        CONTENT_TYPES.get(suffix)
        or mimetypes.guess_type(document.file.name)[0]
        or "application/octet-stream"
    )

    try:
        handle = document.file.open("rb")
    except FileNotFoundError as exc:
        raise Http404("Файл не найден на диске") from exc

    filename = document.original_filename or Path(document.file.name).name
    response = FileResponse(handle, content_type=content_type)
    response["Content-Disposition"] = f"inline; filename*=UTF-8%27%27{quote(filename)}"
    return response


# --- Регистрация ------------------------------------------------------------

def register(request):
    """Простая регистрация без подтверждения почты (этап 8, урезано)."""
    if request.user.is_authenticated:
        return redirect("documents:index")

    if request.method == "POST":
        form = RegistrationForm(request.POST)
        if form.is_valid():
            user = form.save()
            login(request, user)
            messages.success(request, "Аккаунт создан, вы вошли в систему.")
            return redirect("documents:index")
    else:
        form = RegistrationForm()

    return render(request, "registration/register.html", {"form": form})
