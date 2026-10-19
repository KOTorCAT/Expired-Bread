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
)
from .models import Document
from .services.background import recover_stuck, start_processing
from .services.document_service import (
    confirm_document,
    delete_document,
)
from .services.pipeline import PipelineConfig
from .services.qa_service import ask
from .services.search_service import (
    parse_filter_expression,
    parse_filters,
    search_documents,
)

PAGE_SIZE = 20

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
            document = form.save(commit=False)
            document.user = request.user
            document.original_filename = request.FILES["file"].name
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

    return render(
        request,
        "documents/document_detail.html",
        {
            "document": document,
            "warnings": meta.get("warnings", []),
            "corrected_fields": meta.get("corrected_fields", []),
            "is_pdf": suffix == ".pdf",
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

    if request.method == "POST":
        form = DocumentReviewForm(request.POST, instance=document)
        if form.is_valid():
            confirm_document(document.id, form.cleaned_data, user=request.user)
            messages.success(request, "Данные сохранены, документ доступен в поиске.")
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

    # Фильтры из явного блока (несколько полей f)
    filters = parse_filters(request.GET)

    # Если q содержит фильтр (key:op:value) — распознаём
    text_query = query
    if query and not filters:
        inline_filters = parse_filter_expression(query)
        if inline_filters:
            filters = inline_filters
            text_query = ""  # q использован как фильтр

    queryset = search_documents(
        user=request.user,
        query=text_query,
        status=status,
        document_type=document_type,
        filters=filters,
        only_ready=True,
    )

    page, paginator = _page(request, queryset)

    return render(
        request,
        "documents/search_results.html",
        {
            "documents": page.object_list,
            "page_obj": page,
            "paginator": paginator,
            "is_paginated": page.has_other_pages(),
            "total": paginator.count,
            "query": query,
            "status": status,
            "document_type": document_type,
            "filters": filters,
            "type_choices": DOCUMENT_TYPE_CHOICES,
        },
    )


@login_required
def document_ask(request):
    question = ""
    result = None

    if request.method == "POST":
        question = request.POST.get("question", "").strip()
        if question:
            result = ask(question, user=request.user)

    return render(
        request,
        "documents/ask.html",
        {
            "question": question,
            "result": result,
        },
    )


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


# Явные типы: mimetypes на Linux может не знать про docx.
CONTENT_TYPES = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


@login_required
def document_preview(request, pk):
    """Отдаёт оригинал для встроенного просмотра.

    PDF браузер покажет в iframe, DOCX скачается или откроется внешней
    программой — поэтому отдельно отдаётся ещё и download.
    """
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
    response["Content-Disposition"] = f"inline; filename*=UTF-8''{quote(filename)}"
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
