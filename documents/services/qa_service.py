"""Q&A по содержимому документов.

Связывает поиск (search_service) и LLM (llm.answer_question).
"""

from __future__ import annotations

import logging

from documents.models import Document
from documents.services.llm import LLMError, answer_question
from documents.services.search_service import search_documents

logger = logging.getLogger(__name__)

TOP_K = 3
MAX_CHARS_PER_DOC = 3000


def ask(question: str) -> dict:
    """Отвечает на вопрос по содержимому документов.

    Возвращает:
        {
            "found": bool,           # нашла ли система ответ
            "answer": str,           # текст ответа или объяснение
            "sources": list[Document]  # документы, на которых основан ответ
        }
    """
    question = (question or "").strip()
    if not question:
        return {"found": False, "answer": "Пустой вопрос.", "sources": []}

    # Шаг 1. Ищем релевантные документы по ключевым словам.
    # Разбиваем вопрос на слова и ищем документы, где встречается хотя бы одно
    # значимое слово. Если ничего не нашли — не идём в LLM.
    candidates = _find_candidates(question)

    if not candidates:
        return {
            "found": False,
            "answer": (
                "В базе нет документов, подходящих под ваш вопрос. "
                "Попробуйте другие ключевые слова или сначала загрузите документ."
            ),
            "sources": [],
        }

    # Шаг 2. Собираем контекст из top-K документов.
    top_docs = candidates[:TOP_K]
    context = _build_context(top_docs)

    # Шаг 3. Отправляем в LLM.
    try:
        result = answer_question(question, context)
    except LLMError as exc:
        logger.warning("Q&A не удалось: %s", exc)
        return {
            "found": False,
            "answer": f"Не удалось получить ответ от AI: {exc}",
            "sources": top_docs,
        }

    return {
        "found": result["found"],
        "answer": result["answer"],
        "sources": top_docs if result["found"] else [],
    }


# --- Внутренние функции -----------------------------------------------------

def _find_candidates(question: str) -> list[Document]:
    """Ищет документы, релевантные вопросу.

    Простая эвристика: берём все слова длиной >= 3 символов и ищем документы,
    где встречается хотя бы одно из них в title / description / extracted_text.
    """
    words = [
        w for w in question.lower().replace("?", "").replace(",", "").split()
        if len(w) >= 3
    ]
    if not words:
        return []

    results = search_documents(
        query="",
        content_terms=words,
        only_ready=True,
    )
    # Преобразуем QuerySet в список — top-K выберем срезом.
    return list(results[:TOP_K])


def _build_context(documents: list[Document]) -> str:
    """Собирает текст документов в один блок для LLM."""
    parts = []
    for i, doc in enumerate(documents, start=1):
        text = doc.extracted_text or doc.description or ""
        if len(text) > MAX_CHARS_PER_DOC:
            text = text[:MAX_CHARS_PER_DOC] + "…"
        header = f"--- Документ {i}: {doc.title or doc.original_filename} ---"
        parts.append(f"{header}\n{text}")
    return "\n\n".join(parts)