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


def ask(question: str, user=None) -> dict:
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

    candidates = _find_candidates(question, user=user)

    if not candidates:
        return {
            "found": False,
            "answer": (
                "В базе нет документов, подходящих под ваш вопрос. "
                "Попробуйте другие ключевые слова или сначала загрузите документ."
            ),
            "sources": [],
        }

    top_docs = candidates[:TOP_K]
    context = _build_context(top_docs, question)

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

def _find_candidates(question: str, user=None) -> list[Document]:
    """Ищет документы, релевантные вопросу.

    Берём все слова длиной >= 4 символов (включая латиницу и цифры),
    ищем документы, где встречается хотя бы одно.
    """
    import re

    # Разбиваем вопрос на слова: буквы, цифры, дефис, подчёркивание
    words = re.findall(r"[\w\-]+", question.lower(), flags=re.UNICODE)
    # Убираем короткие и стоп-слова
    words = [w for w in words if len(w) >= 4]

    if not words:
        return []

    results = search_documents(
        user=user,
        query="",
        content_terms=words,
        only_ready=True,
    )
    return list(results[:TOP_K])


def _build_context(documents: list[Document], question: str = "") -> str:
    """Собирает контекст из документов.

    Вместо «первых N символов» берём окрестности ключевых слов
    из вопроса. Это важно для длинных документов (например, резюме
    на 6000+ символов), где нужная информация может быть в конце.
    """
    parts = []
    for i, doc in enumerate(documents, start=1):
        text = doc.extracted_text or doc.description or ""
        excerpt = _extract_relevant_excerpt(text, question, MAX_CHARS_PER_DOC)
        header = f"--- Документ {i}: {doc.title or doc.original_filename} ---"
        parts.append(f"{header}\n{excerpt}")
    return "\n\n".join(parts)


def _extract_relevant_excerpt(text: str, question: str, max_chars: int = 3000) -> str:
    """Возвращает фрагмент текста вокруг ключевых слов вопроса.

    Алгоритм:
      1. Извлекаем слова вопроса (длиной >= 4).
      2. Исключаем служебные слова («есть», «меня», «проект» и т.п.).
      3. Сортируем оставшиеся по длине — самые длинные важнее.
      4. Ищем окно вокруг вхождения самого длинного слова.
      5. Если ничего не нашли — возвращаем начало текста.
    """
    import re

    if not text:
        return ""

    if len(text) <= max_chars:
        return text

    if not question:
        return text[:max_chars]

    words = re.findall(r"[\w\-]+", question.lower(), flags=re.UNICODE)
    words = [w for w in words if len(w) >= 4]

    # Стоп-слова: местоимения, глаголы-связки, общие слова
    stop_words = {
        "есть", "было", "были", "был", "была", "меня", "тебя", "него",
        "неё", "него", "вами", "нами", "какие", "какой", "какая",
        "какое", "какие", "этот", "эта", "это", "эти", "того", "этому",
        "проект", "проекты", "документ", "документы", "информация",
        "содержится", "указано", "написано", "упоминается", "найти",
        "покажи", "скажи", "напиши", "расскажи", "список", "перечень",
        "данные", "данных", "можно", "нужно", "надо", "хочу", "хотел",
    }
    words = [w for w in words if w not in stop_words]

    if not words:
        return text[:max_chars]

    # Сортируем по длине (от длинных к коротким) — самые специфичные важнее
    words = sorted(set(words), key=len, reverse=True)

    text_lower = text.lower()

    # Ищем позиции слов, начиная с самого длинного
    for word in words:
        positions = []
        pos = text_lower.find(word)
        while pos >= 0:
            positions.append(pos)
            pos = text_lower.find(word, pos + 1)

        if not positions:
            continue

        # Нашли слово — берём окно вокруг его первого вхождения
        center = positions[0]
        start = max(0, center - max_chars // 6)   # 500 символов до
        end = start + max_chars

        if end > len(text):
            end = len(text)
            start = max(0, end - max_chars)

        # Добавляем начало текста, если оно не входит
        if start > 500:
            prefix = text[:500]
            excerpt = text[start:end]
            return f"{prefix}\n...\n{excerpt}"

        return text[start:end]

    return text[:max_chars]