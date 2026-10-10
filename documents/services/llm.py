"""Транспорт к GigaChat.

Модуль ничего не знает про типы документов, схемы Pydantic и бизнес-логику.
Единственная задача — отправить запрос, получить ответ, распарсить JSON,
вернуть (data, usage).

Все высокоуровневые функции — classify, extract, repair — живут в pipeline.py.
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
from json_repair import repair_json

load_dotenv()

logger = logging.getLogger(__name__)


class LLMError(Exception):
    """Ошибка при работе с GigaChat: сеть, пустой ответ, невалидный JSON."""


MAX_RETRIES = 2
RETRY_DELAY_SECONDS = 2.0
DEFAULT_TIMEOUT = 60.0


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


def get_default_model() -> str:
    return os.environ.get("GIGACHAT_MODEL", "GigaChat-2-Max")


# --- Основной транспорт -----------------------------------------------------

def json_chat(
    system: str,
    user: str,
    json_schema: dict[str, Any],
    *,
    model: str | None = None,
    attachments: list[str] | None = None,
    max_tokens: int | None = None,
    temperature: float | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Отправляет запрос в GigaChat с JSON Schema.

    Возвращает (parsed_json, usage_dict).
    usage_dict — пустой словарь, если API не вернул usage.

    Бросает LLMError при:
      - сетевой ошибке (после ретраев),
      - пустом ответе,
      - невалидном JSON даже после ремонта.
    """
    if not user or not user.strip():
        raise LLMError("Пустой user-промпт")

    model_name = model or get_default_model()

    messages = [
        Messages(role=MessagesRole.SYSTEM, content=system),
        Messages(role=MessagesRole.USER, content=user),
    ]

    chat_kwargs: dict[str, Any] = {
        "model": model_name,
        "messages": messages,
        "response_format": {
            "type": "json_schema",
            "schema": json_schema,
            "strict": True,
        },
    }

    if attachments:
        messages[-1].attachments = attachments

    if max_tokens is not None:
        chat_kwargs["max_tokens"] = max_tokens

    if temperature is not None:
        chat_kwargs["temperature"] = temperature

    chat = Chat(**chat_kwargs)

    last_error: Exception | None = None

    for attempt in range(MAX_RETRIES + 1):
        try:
            with _get_client() as client:
                response = client.chat(chat)

            content = response.choices[0].message.content
            if not content:
                raise LLMError("Модель вернула пустой ответ")

            parsed = _parse_json_tolerant(content)
            if parsed is None:
                logger.error(
                    "Не удалось распарсить ответ модели (первые 500):\n%s",
                    content[:500],
                )
                raise LLMError(
                    f"Ответ модели не JSON даже после ремонта. "
                    f"Первые 200 символов: {content[:200]}"
                )

            usage = _extract_usage(response)
            return parsed, usage

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


def _parse_json_tolerant(content: str) -> dict[str, Any] | None:
    """Парсит JSON с ремонтом.

    GigaChat иногда возвращает «почти JSON»: висячие запятые,
    одинарные кавычки, комментарии, неполные строки. json-repair
    чинит такие ошибки перед парсингом.

    Возвращает dict или None, если даже ремонт не помог.
    """
    # Сначала стандартный парсинг
    try:
        parsed = json.loads(content)
        if isinstance(parsed, dict):
            return parsed
        return None
    except json.JSONDecodeError:
        pass

    # Пробуем json-repair
    try:
        repaired = repair_json(content, return_objects=True)
        if isinstance(repaired, dict):
            logger.info("JSON был починен через json-repair")
            return repaired
    except Exception as exc:
        logger.warning("json-repair не смог починить JSON: %s", exc)

    return None


def _extract_usage(response) -> dict[str, Any]:
    """Извлекает usage из ответа, если он есть."""
    if not hasattr(response, "usage") or not response.usage:
        return {}

    usage = response.usage
    return {
        "prompt_tokens": getattr(usage, "prompt_tokens", None),
        "completion_tokens": getattr(usage, "completion_tokens", None),
        "total_tokens": getattr(usage, "total_tokens", None),
        "precached_prompt_tokens": getattr(usage, "precached_prompt_tokens", 0),
    }


# --- Работа с файлами (для Vision) -----------------------------------------

def upload_file(file_path: str | os.PathLike) -> str:
    """Загружает файл в GigaChat Files API. Возвращает file_id."""
    path = Path(file_path)
    if not path.exists():
        raise LLMError(f"Файл не найден: {path}")

    try:
        with _get_client() as client:
            uploaded = client.upload_file(file=str(path), purpose="general")
        return uploaded.id_
    except Exception as exc:
        raise LLMError(f"Не удалось загрузить файл в GigaChat: {exc}") from exc


def delete_file(file_id: str) -> None:
    """Удаляет файл из GigaChat Files API."""
    try:
        with _get_client() as client:
            client.delete_file(file_id)
    except Exception as exc:
        logger.warning("Не удалось удалить файл %s из GigaChat: %s", file_id, exc)


# --- Очистка артефактов форматирования --------------------------------------

_FORMATTING_PREFIXES = "!*$%&#@>•-—{}[]/\\"


def clean_text_artifacts(value: Any) -> Any:
    """Убирает мусорные символы форматирования из значений.

    GigaChat иногда добавляет !, *, $, %, &, #, •, -, —, {}, [], / в
    начало/конец строк, имитируя Markdown или шаблонизацию.
    Убираем их. Пустые строки удаляются из списков.

    Обрабатывает рекурсивно: str, list, dict.
    """
    if isinstance(value, str):
        s = value.strip()
        # Повторяем, пока не перестанут убираться символы
        prev = None
        while prev != s:
            prev = s
            s = s.strip(_FORMATTING_PREFIXES)
            s = s.strip()
        # Заменяем случайные русские буквы в начале латинских слов
        s = _fix_cyrillic_latin_mix(s)
        return s

    if isinstance(value, list):
        value = try_parse_string_list(value)
        cleaned = [clean_text_artifacts(v) for v in value]
        # Разбиваем «Категория: a, b, c» на отдельные элементы
        cleaned = split_categorized_items(cleaned)
        # Убираем пустые
        cleaned = [
            v for v in cleaned
            if not (isinstance(v, str) and not v.strip())
        ]
        return cleaned

    if isinstance(value, dict):
        return {k: clean_text_artifacts(v) for k, v in value.items()}

    return value


# Карта похожих символов: кириллица → латиница
# (только для случаев, где GigaChat ошибочно заменил)
_CYRILLIC_TO_LATIN = {
    "А": "A", "В": "B", "С": "C", "Е": "E", "Н": "H", "К": "K",
    "М": "M", "О": "O", "П": "P", "Р": "P", "Т": "T", "Х": "X",
    "У": "Y", "І": "I", "Ј": "J",
    "а": "a", "в": "b", "с": "c", "е": "e", "н": "h", "к": "k",
    "м": "m", "о": "o", "п": "p", "р": "p", "т": "t", "х": "x",
    "у": "y", "і": "i", "ј": "j",
}


def _fix_cyrillic_latin_mix(s: str) -> str:
    """Заменяет кириллические буквы, похожие на латинские, в начале слов.

    GigaChat иногда возвращает 'Пython' вместо 'Python' или
    'С++' вместо 'C++'. Здесь мы пытаемся это починить:
    если слово начинается с заглавной кириллицы, похожей на латиницу,
    и остальные буквы — латиница, заменяем первую.
    """
    if not s:
        return s

    # Первая буква — заглавная кириллица, похожая на латиницу
    first = s[0]
    if first not in _CYRILLIC_TO_LATIN:
        return s

    # Остальные буквы преимущественно латиница
    rest = s[1:]
    if not rest:
        return s

        # Считаем кириллические буквы в остатке. Если их нет — заменяем
    # первую букву на латинскую. Если есть — оставляем как есть
    # (значит, это настоящее русское слово).
    cyr_count = sum(1 for c in rest if c.isalpha() and not c.isascii())
    if cyr_count == 0:
        return _CYRILLIC_TO_LATIN[first] + rest
    return s


# --- Утилита: очистка ключей ------------------------------------------------

def clean_keys(data: dict[str, Any]) -> dict[str, Any]:
    """Очищает ключи от служебных префиксов, которые добавляет GigaChat.

    GigaChat иногда добавляет:
      - '../../../' — путь к полю в схеме;
      - '_' в начале ключа;
      - '__comment_*' и '_comment_*' — служебные комментарии.

    После очистки ключей применяет clean_text_artifacts к значениям.
    """
    cleaned: dict[str, Any] = {}

    for key, value in data.items():
        new_key = _clean_key(key)
        if not new_key:
            continue
        if new_key.startswith("__") or new_key.startswith("_comment"):
            continue
        if new_key in cleaned:
            old = cleaned[new_key]
            if old in ("", [], {}, None) and value not in ("", [], {}, None):
                cleaned[new_key] = value
        else:
            cleaned[new_key] = value

    # Чистим значения от артефактов форматирования
    return clean_text_artifacts(cleaned)


def _clean_key(key: str) -> str:
    """Убирает служебные префиксы из имени поля."""
    while key.startswith("../"):
        key = key[3:]
    while key.startswith("-"):
        key = key[1:]
    while key.startswith("_") and not key.startswith("_comment"):
        key = key[1:]
    return key


# --- Q&A: ответ на вопрос по контексту --------------------------------------

QA_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "found": {
            "type": "boolean",
            "description": (
                "true, если в предоставленных документах есть ответ. "
                "false, если ответа нет."
            ),
        },
        "answer": {
            "type": "string",
            "description": "Ответ на русском. Если found=false — объясни, чего не хватает.",
        },
    },
    "required": ["found", "answer"],
    "additionalProperties": False,
}

QA_SYSTEM_PROMPT = (
    "Ты — ассистент, который отвечает на вопросы строго по предоставленным "
    "документам.\n\n"
    "ПРАВИЛА:\n"
    "1. Отвечай ТОЛЬКО на основе текста документов.\n"
    "2. Если ответа нет — верни found=false и коротко объясни.\n"
    "3. НЕ придумывай факты.\n"
    "4. Отвечай на русском, кратко.\n"
    "Верни JSON строго по схеме."
)


def answer_question(question: str, documents_context: str) -> dict[str, Any]:
    """Отвечает на вопрос по предоставленному контексту.

    Возвращает {"found": bool, "answer": str}.
    """
    if not question or not question.strip():
        raise LLMError("Пустой вопрос")
    if not documents_context or not documents_context.strip():
        raise LLMError("Пустой контекст")

    user_message = (
        f"Вопрос пользователя:\n{question}\n\n"
        f"Тексты документов:\n{documents_context}"
    )

    parsed, _usage = json_chat(
        system=QA_SYSTEM_PROMPT,
        user=user_message,
        json_schema=QA_JSON_SCHEMA,
    )

    if "found" not in parsed or not isinstance(parsed["found"], bool):
        raise LLMError("В ответе нет корректного поля found")
    if "answer" not in parsed or not isinstance(parsed["answer"], str):
        raise LLMError("В ответе нет корректного поля answer")

    return {"found": parsed["found"], "answer": parsed["answer"]}

# --- Распаковка вложенных списков -------------------------------------------

def try_parse_string_list(value: Any) -> Any:
    """Если значение — список из одной строки, где строка похожа на список,
    распаковывает её.

    GigaChat иногда возвращает:
        ["['Python', 'C', 'JavaScript']"]     # список внутри строки
    вместо:
        ["Python", "C", "JavaScript"]

    Эта функция пытается распаковать такое.

    Работает безопасно: если не получилось — возвращает значение как есть.
    """
    import ast

    if not isinstance(value, list):
        return value

    # Только если в списке ровно один элемент и это строка
    if len(value) != 1 or not isinstance(value[0], str):
        return value

    s = value[0].strip()

    # Быстрая проверка: похоже ли на список
    if not (s.startswith("[") and s.endswith("]")):
        return value

    try:
        parsed = ast.literal_eval(s)
        if isinstance(parsed, list) and all(isinstance(x, str) for x in parsed):
            return parsed
    except (ValueError, SyntaxError):
        pass

    return value

def split_categorized_items(value: Any) -> Any:
    """Разбивает элементы списка по запятым, если они склеены.

    GigaChat иногда возвращает:
        ['Python, C, JavaScript']     (склеено через запятую)
        ['Фреймворки: Qt5, NumPy']    (с категорией)
    вместо:
        ['Python', 'C', 'JavaScript']

    Эта функция разбивает по запятым и убирает категорию (текст до ':').
    """
    if not isinstance(value, list):
        return value

    result = []
    for item in value:
        if not isinstance(item, str):
            result.append(item)
            continue

        s = item.strip()

        # Если есть ':' — отрезаем категорию
        if ":" in s:
            _, s = s.split(":", 1)
            s = s.strip()

        # Если есть запятые — разбиваем
        if "," in s:
            parts = [p.strip() for p in s.split(",") if p.strip()]
            if len(parts) > 1:
                result.extend(parts)
                continue

        result.append(s)

    return result