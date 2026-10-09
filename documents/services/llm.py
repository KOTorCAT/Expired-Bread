"""Работа с GigaChat: извлечение структурированных данных из текста и файлов.

Модуль не знает о Django-моделях и не сохраняет ничего в БД.
Вход — текст или путь к файлу, выход — словарь Python.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from gigachat import GigaChat
from gigachat.models import Chat, Messages, MessagesRole

load_dotenv()

logger = logging.getLogger(__name__)


class LLMError(Exception):
    """Не удалось получить или разобрать ответ LLM."""


# --- JSON-схема, которую мы ждём от модели ---------------------------------

DOCUMENT_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "document_type": {
            "type": "string",
            "description": (
                "Тип документа: resume, price_list, report, contract, letter, other"
            ),
        },
        "title": {
            "type": "string",
            "description": "Краткое человекочитаемое название документа",
        },
        "description": {
            "type": "string",
            "description": "Одно-два предложения о содержании документа",
        },
        "candidate_name": {
            "type": "string",
            "description": "Имя человека (для резюме, писем, договоров)",
        },
        "age": {
            "type": "integer",
            "description": "Возраст (для резюме)",
        },
        "city": {"type": "string"},
        "email": {"type": "string"},
        "phone": {"type": "string"},
        "position": {
            "type": "string",
            "description": "Желаемая должность или текущая роль",
        },
        "skills": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Список навыков (для резюме)",
        },
        "experience": {
            "type": "array",
            "items": {"type": "object"},
            "description": "Опыт работы: список мест с должностями и годами",
        },
        "education": {
            "type": "array",
            "items": {"type": "object"},
            "description": "Образование: список учебных заведений",
        },
        "languages": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
    "required": ["document_type", "title"],
    "additionalProperties": True,
}

SYSTEM_PROMPT = (
    "Ты — помощник по обработке документов. "
    "Проанализируй документ и верни JSON строго по схеме. "
    "Поле document_type — одно слово на английском: "
    "resume, price_list, report, contract, letter, other. "
    "Поле title — короткое название документа. "
    "Поле description — 1-2 предложения о содержании. "
    "\n\n"
    "ВАЖНО: извлекай как можно больше полезной структурированной информации, "
    "специфичной для типа документа. Примеры:\n"
    "- resume: candidate_name, age, city, email, phone, position, "
    "skills (список), experience (список мест работы с должностями и годами), "
    "education (список учебных заведений), languages.\n"
    "- price_list: items (список товаров с ценами), currency, vendor.\n"
    "- contract: parties (стороны), subject, amount, start_date, end_date.\n"
    "- report: author, organization, discipline, period, key_findings.\n"
    "- letter: sender, recipient, subject, date.\n"
    "\n"
    "Не добавляй пояснений вне JSON. Не используй служебные поля "
    "с двойным подчёркиванием в начале."
        "\n"
    "ВАЖНО: перечисли ВСЕ навыки, ВСЕ места работы, ВСЁ образование, "
    "все языки, которые упоминаются в тексте. Не пропускай ничего. "
    "Если в документе 20 навыков — верни 20. Если 15 мест работы — верни 15."
)
MAX_RETRIES = 2
RETRY_DELAY_SECONDS = 2.0


# --- Клиент -----------------------------------------------------------------

def _get_client() -> GigaChat:
    credentials = os.environ.get("GIGACHAT_CREDENTIALS")
    scope = os.environ.get("GIGACHAT_SCOPE", "GIGACHAT_API_PERS")
    ca_bundle = os.environ.get("GIGACHAT_CA_BUNDLE_FILE")
    verify_ssl = os.environ.get("GIGACHAT_VERIFY_SSL_CERTS", "True").lower() == "true"

    if not credentials:
        raise LLMError("GIGACHAT_CREDENTIALS не задан в окружении")

    return GigaChat(
        credentials=credentials,
        scope=scope,
        ca_bundle_file=ca_bundle,
        verify_ssl_certs=verify_ssl,
    )


def _get_model() -> str:
    return os.environ.get("GIGACHAT_MODEL", "GigaChat-2-Max")


# --- Публичные функции ------------------------------------------------------

def analyze_document(text: str) -> dict[str, Any]:
    """Отправляет текст в GigaChat и возвращает структурированный словарь."""
    if not text or not text.strip():
        raise LLMError("Пустой текст: нечего анализировать")

    chat = Chat(
        model=_get_model(),
        messages=[
            Messages(role=MessagesRole.SYSTEM, content=SYSTEM_PROMPT),
            Messages(role=MessagesRole.USER, content=text),
        ],
        response_format={
            "type": "json_schema",
            "schema": DOCUMENT_JSON_SCHEMA,
            "strict": True,
        },
    )

    return _call_with_retries(chat)


def analyze_document_vision(file_path: str | os.PathLike) -> dict[str, Any]:
    """Загружает файл в GigaChat Files API и анализирует его через Vision."""
    path = Path(file_path)
    if not path.exists():
        raise LLMError(f"Файл не найден: {path}")

    with _get_client() as client:
        try:
            uploaded = client.upload_file(file=str(path), purpose="general")
        except Exception as exc:
            raise LLMError(f"Не удалось загрузить файл в GigaChat: {exc}") from exc

        try:
            chat = Chat(
                model=_get_model(),
                messages=[
                    Messages(role=MessagesRole.SYSTEM, content=SYSTEM_PROMPT),
                    Messages(
                        role=MessagesRole.USER,
                        content="Проанализируй приложенный документ.",
                        attachments=[uploaded.id_],
                    ),
                ],
                response_format={
                    "type": "json_schema",
                    "schema": DOCUMENT_JSON_SCHEMA,
                    "strict": True,
                },
            )
            return _call_with_retries(chat, client=client)
        finally:
            # Подчищаем файл, чтобы не копить мусор в облаке
            try:
                client.delete_file(uploaded.id_)
            except Exception:
                logger.warning("Не удалось удалить файл %s из GigaChat", uploaded.id_)


def _clean_key(key: str) -> str:
    """Убирает служебные префиксы из имён полей.

    GigaChat иногда добавляет '../../../', '_', '-_' и другой мусор.
    После очистки ключ может стать пустым — тогда возвращаем ''.
    """
    while key.startswith("../"):
        key = key[3:]
    while key.startswith("-"):
        key = key[1:]
    while key.startswith("_") and not key.startswith("_comment"):
        key = key[1:]
    return key

def validate_document_data(data: dict[str, Any]) -> dict[str, Any]:
    """Проверяет структуру ответа. Бросает LLMError, если что-то не так."""
    if not isinstance(data, dict):
        raise LLMError(f"Ожидался dict, получено {type(data).__name__}")

    cleaned: dict[str, Any] = {}
    for k, v in data.items():
        new_key = _clean_key(k)
        # Пустой ключ или служебный комментарий — выкидываем
        if not new_key or new_key.startswith("__") or new_key.startswith("_comment"):
            continue
        # При коллизии предпочитаем непустое значение
        if new_key in cleaned and cleaned[new_key] in ("", [], {}, None) and v not in ("", [], {}, None):
            cleaned[new_key] = v
        elif new_key not in cleaned:
            cleaned[new_key] = v

    if not cleaned.get("document_type"):
        raise LLMError("В ответе отсутствует document_type")
    if not isinstance(cleaned["document_type"], str):
        raise LLMError("document_type должен быть строкой")
    if not cleaned.get("title"):
        raise LLMError("В ответе отсутствует title")
    if not isinstance(cleaned["title"], str):
        raise LLMError("title должен быть строкой")
    if "description" in cleaned and not isinstance(cleaned["description"], str):
        raise LLMError("description должен быть строкой")

    return cleaned

# --- Внутренняя кухня -------------------------------------------------------

def _call_with_retries(chat: Chat, client: GigaChat | None = None) -> dict[str, Any]:
    last_error: Exception | None = None

    for attempt in range(MAX_RETRIES + 1):
        try:
            if client is not None:
                response = client.chat(chat)
            else:
                with _get_client() as c:
                    response = c.chat(chat)

            content = response.choices[0].message.content
            if not content:
                raise LLMError("Модель вернула пустой ответ")

            try:
                parsed = json.loads(content)
            except json.JSONDecodeError as exc:
                raise LLMError(f"Ответ модели не является валидным JSON: {exc}") from exc

            return validate_document_data(parsed)

        except LLMError:
            raise
        except Exception as exc:
            last_error = exc
            logger.warning(
                "Попытка %d/%d не удалась: %s",
                attempt + 1, MAX_RETRIES + 1, exc,
            )
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY_SECONDS)

    raise LLMError(f"Не удалось получить ответ от GigaChat: {last_error}")


# --- Q&A: ответы на вопросы по содержимому документов -----------------------

QA_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "found": {
            "type": "boolean",
            "description": (
                "true, если в предоставленных документах есть ответ на вопрос. "
                "false, если ответа нет или информации недостаточно."
            ),
        },
        "answer": {
            "type": "string",
            "description": (
                "Ответ на вопрос пользователя на русском языке. "
                "Если found=false, кратко объясни, что информации нет."
            ),
        },
    },
    "required": ["found", "answer"],
    "additionalProperties": False,
}

QA_SYSTEM_PROMPT = (
    "Ты — ассистент, который отвечает на вопросы пользователя строго по "
    "предоставленным документам.\n\n"
    "ПРАВИЛА:\n"
    "1. Отвечай ТОЛЬКО на основе текста документов ниже.\n"
    "2. Если ответа в документах нет или информации недостаточно — "
    "верни found=false и коротко объясни, чего не хватает.\n"
    "3. НЕ придумывай факты, которых нет в документах.\n"
    "4. Если ответ есть — верни found=true и конкретный ответ.\n"
    "5. Отвечай на русском языке, кратко и по существу.\n"
    "Верни JSON строго по схеме."
)


def answer_question(question: str, documents_context: str) -> dict[str, Any]:
    """Отвечает на вопрос по предоставленному контексту.

    documents_context — уже собранный текст документов с разделителями.
    Возвращает {"found": bool, "answer": str}.
    """
    if not question or not question.strip():
        raise LLMError("Пустой вопрос")

    if not documents_context or not documents_context.strip():
        raise LLMError("Пустой контекст: нечего анализировать")

    user_message = (
        f"Вопрос пользователя:\n{question}\n\n"
        f"Тексты документов:\n{documents_context}"
    )

    chat = Chat(
        model=_get_model(),
        messages=[
            Messages(role=MessagesRole.SYSTEM, content=QA_SYSTEM_PROMPT),
            Messages(role=MessagesRole.USER, content=user_message),
        ],
        response_format={
            "type": "json_schema",
            "schema": QA_JSON_SCHEMA,
            "strict": True,
        },
    )

    return _call_qa_with_retries(chat)


def _call_qa_with_retries(chat: Chat) -> dict[str, Any]:
    """Как _call_with_retries, но валидирует ответ по QA-схеме."""
    last_error: Exception | None = None

    for attempt in range(MAX_RETRIES + 1):
        try:
            with _get_client() as client:
                response = client.chat(chat)

            content = response.choices[0].message.content
            if not content:
                raise LLMError("Модель вернула пустой ответ")

            try:
                parsed = json.loads(content)
            except json.JSONDecodeError as exc:
                raise LLMError(f"Ответ модели не JSON: {exc}") from exc

            if not isinstance(parsed, dict):
                raise LLMError("Ответ модели не является объектом")
            if "found" not in parsed or not isinstance(parsed["found"], bool):
                raise LLMError("В ответе нет корректного поля found")
            if "answer" not in parsed or not isinstance(parsed["answer"], str):
                raise LLMError("В ответе нет корректного поля answer")

            return {"found": parsed["found"], "answer": parsed["answer"]}

        except LLMError:
            raise
        except Exception as exc:
            last_error = exc
            logger.warning(
                "Попытка %d/%d Q&A не удалась: %s",
                attempt + 1, MAX_RETRIES + 1, exc,
            )
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY_SECONDS)

    raise LLMError(f"Не удалось получить ответ от GigaChat: {last_error}")