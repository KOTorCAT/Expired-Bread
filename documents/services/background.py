"""Фоновая обработка документа.

Зачем: вызов GigaChat занимает десятки секунд, и держать на нём
HTTP-ответ нельзя — страница «висит», браузер ждёт, пользователь
не понимает, работает система или упала.

Здесь обработка уходит в отдельный поток: представление отвечает сразу
со статусом «в очереди», а страница документа сама опрашивает статус
и перерисовывается, когда он меняется.

Это НЕ полноценная очередь (этап 10 из ТЗ): нет персистентности,
повторного запуска после падения процесса и ограничения параллельности.
Для демонстрации этого достаточно; для эксплуатации нужен воркер.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from threading import Lock, Thread

from django.db import close_old_connections, connection
from django.utils import timezone

from documents.models import Document

logger = logging.getLogger(__name__)

# Сколько минут обработка считается нормальной, прежде чем признать зависание.
# Запас большой: документ может ждать очереди, пока модель занята другим.
STUCK_AFTER_MINUTES = 10

# На бесплатном тарифе GigaChat разрешён один одновременный запрос.
# Второй поток не ускорит работу, а получит 429 и «съест» лимит,
# поэтому запросы к модели выстраиваются в очередь этим замком.
_MODEL_LOCK = Lock()


def start_processing(document_id: int, config=None) -> bool:
    """Захватывает документ и запускает обработку в фоне.

    Возвращает False, если документ уже обрабатывается — повторный клик
    по «Повторить» не должен запускать вторую параллельную обработку.
    """
    claimed = (
        Document.objects.filter(pk=document_id)
        .exclude(status=Document.Status.PROCESSING)
        .update(
            status=Document.Status.PROCESSING,
            error_message="",
            updated_at=timezone.now(),
        )
    )

    if not claimed:
        logger.info("Документ %s уже обрабатывается — второй запуск не нужен", document_id)
        return False

    thread = Thread(
        target=_process_in_thread,
        args=(document_id, config),
        name=f"document-{document_id}",
        daemon=True,
    )
    thread.start()

    logger.info("Документ %s отправлен на обработку в фоне", document_id)
    return True


def _process_in_thread(document_id: int, config=None) -> None:
    """Тело фонового потока.

    Соединение с БД у потока своё: открываем его заново и закрываем
    в конце, иначе воркер держит соединение до перезапуска процесса.
    """
    close_old_connections()
    try:
        # Импорт внутри функции: при импорте модуля Django может быть ещё
        # не готов, а сервис нужен только в момент запуска.
        from documents.services.document_service import process_document

        with _MODEL_LOCK:
            # Пока документ ждал очереди, он выглядел «зависшим» —
            # обновляем отметку времени, чтобы восстановление его не тронуло.
            Document.objects.filter(pk=document_id).update(updated_at=timezone.now())
            process_document(document_id, config=config)
    except Exception:  # noqa: BLE001 — поток не должен уносить процесс
        logger.exception("Фоновая обработка документа %s завершилась ошибкой", document_id)
        _mark_failed(
            document_id,
            "Неожиданная ошибка при обработке. Нажмите «Повторить обработку».",
        )
    finally:
        connection.close()


def recover_stuck(document: Document) -> bool:
    """Снимает документ с «обрабатывается», если он там завис.

    Без этого статус навсегда остаётся «Обрабатывается», если процесс
    перезапустили посреди работы. Возвращает True, если статус изменён.
    """
    if document.status != Document.Status.PROCESSING:
        return False

    deadline = timezone.now() - timedelta(minutes=STUCK_AFTER_MINUTES)
    if document.updated_at > deadline:
        return False

    document.status = Document.Status.FAILED
    document.error_message = (
        "Обработка прервана: превышено время ожидания. "
        "Нажмите «Повторить обработку»."
    )
    document.save(update_fields=["status", "error_message", "updated_at"])

    logger.warning("Документ %s помечен как зависший", document.pk)
    return True


def _mark_failed(document_id: int, message: str) -> None:
    try:
        Document.objects.filter(pk=document_id).update(
            status=Document.Status.FAILED,
            error_message=message,
            updated_at=timezone.now(),
        )
    except Exception:  # noqa: BLE001 — уже нечего чинить
        logger.exception("Не удалось пометить документ %s как ошибочный", document_id)
