from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render

from .forms import DocumentReviewForm, DocumentUploadForm
from .models import Document
from .services.document_service import confirm_document, process_document
from .services.search_service import search_documents
from .services.qa_service import ask


def index(request):
    return render(request, "documents/index.html")


def document_list(request):
    documents = Document.objects.all()
    return render(
        request,
        "documents/document_list.html",
        {"documents": documents},
    )


def document_upload(request):
    if request.method == "POST":
        form = DocumentUploadForm(request.POST, request.FILES)
        if form.is_valid():
            document = form.save(commit=False)
            document.original_filename = request.FILES["file"].name
            document.status = Document.Status.UPLOADED
            document.save()

            # Синхронная обработка. На этапе 10 можно вынести в Celery.
            process_document(document.id)
            return redirect("documents:detail", pk=document.pk)
    else:
        form = DocumentUploadForm()

    return render(
        request,
        "documents/document_upload.html",
        {"form": form},
    )


def document_detail(request, pk):
    document = get_object_or_404(Document, pk=pk)
    return render(
        request,
        "documents/document_detail.html",
        {"document": document},
    )


def document_review(request, pk):
    document = get_object_or_404(Document, pk=pk)

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
            confirm_document(document.id, form.cleaned_data)
            return redirect("documents:detail", pk=document.pk)
    else:
        form = DocumentReviewForm(instance=document)

    return render(
        request,
        "documents/document_review.html",
        {"document": document, "form": form},
    )


def document_reprocess(request, pk):
    """Повторный запуск обработки. Только POST."""
    document = get_object_or_404(Document, pk=pk)
    if request.method == "POST":
        process_document(document.id)
    return redirect("documents:detail", pk=document.pk)


def document_search(request):
    from documents.services.search_service import (
        parse_filters,
        parse_filter_expression,
    )

    q = request.GET.get("q", "").strip()
    status = request.GET.get("status", "").strip()
    document_type = request.GET.get("type", "").strip()

    # Фильтры из явного блока (несколько полей f)
    filters = parse_filters(request.GET)

    # Если q содержит фильтр (key:op:value) — распознаём
    text_query = q
    if q and not filters:
        inline_filters = parse_filter_expression(q)
        if inline_filters:
            filters = inline_filters
            text_query = ""  # q использован как фильтр

    documents = search_documents(
        query=text_query,
        status=status,
        document_type=document_type,
        filters=filters,
        only_ready=True,
    )

    return render(
        request,
        "documents/search_results.html",
        {
            "documents": documents,
            "query": q,
            "status": status,
            "document_type": document_type,
            "filters": filters,
        },
    )


def document_ask(request):
    question = ""
    result = None

    if request.method == "POST":
        question = request.POST.get("question", "").strip()
        if question:
            result = ask(question)

    return render(
        request,
        "documents/ask.html",
        {
            "question": question,
            "result": result,
        },
    )