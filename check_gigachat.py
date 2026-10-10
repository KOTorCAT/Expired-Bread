"""Разовая проверка GigaChat: usage, Pydantic-схемы, токенизация.

Запуск:
    python check_gigachat.py

Ничего не пишет в БД, не трогает проект. Только проверяет API.
"""

import json
import os
from pathlib import Path

from dotenv import load_dotenv
from gigachat import GigaChat
from gigachat.models import Chat, Messages, MessagesRole

load_dotenv()


def print_section(title: str) -> None:
    print()
    print("=" * 70)
    print(title)
    print("=" * 70)


def main() -> None:
    credentials = os.environ.get("GIGACHAT_CREDENTIALS")
    scope = os.environ.get("GIGACHAT_SCOPE", "GIGACHAT_API_PERS")
    ca_bundle = os.environ.get("GIGACHAT_CA_BUNDLE_FILE")
    verify_ssl = os.environ.get("GIGACHAT_VERIFY_SSL_CERTS", "True").lower() == "true"
    model = os.environ.get("GIGACHAT_MODEL", "GigaChat-2-Max")

    if not credentials:
        print("ОШИБКА: GIGACHAT_CREDENTIALS не задан в .env")
        return

    print(f"Модель: {model}")
    print(f"Scope:  {scope}")
    print(f"CA:     {ca_bundle}")

    with GigaChat(
        credentials=credentials,
        scope=scope,
        ca_bundle_file=ca_bundle,
        verify_ssl_certs=verify_ssl,
    ) as client:
        # --- Проверка 1: простой запрос + usage ----------------------------
        print_section("ПРОВЕРКА 1: простой запрос и поле usage")

        chat = Chat(
            model=model,
            messages=[
                Messages(
                    role=MessagesRole.USER,
                    content="Ответь одним словом: сколько будет два плюс два?",
                ),
            ],
        )
        response = client.chat(chat)

        print("Сырой ответ (dict):")
        print(json.dumps(response.model_dump(), ensure_ascii=False, indent=2))

        print()
        print(f"Поле 'usage' присутствует: {hasattr(response, 'usage')}")
        if hasattr(response, "usage"):
            print(f"usage = {response.usage}")

        # --- Проверка 2: реальная токенизация ------------------------------
        print_section("ПРОВЕРКА 2: токенизация русского текста")

        # Возьмём реальный кусок русского текста — заведомо длинный
        sample = (
            "Асинхронное программирование на Python с применением Flask и asyncio. "
            "JSONPlaceholder — бесплатный фейковый REST API для тестирования. "
            "Предоставляет ресурсы: /users, /posts. API поддерживает фильтрацию "
            "через query-параметры. Смирнов Михаил Борисович, 20 лет, "
            "начинающий разработчик. Навыки: Python, C, JavaScript, HTML, CSS, SQL. "
            "Образование: РГПУ им. Герцена (бакалавриат, 2023–2027), Школа 21 (Сбер)."
        )
        print(f"Символов в тексте: {len(sample)}")

        chat = Chat(
            model=model,
            messages=[
                Messages(
                    role=MessagesRole.USER,
                    content=f"Перепиши этот текст одной строкой, ничего не сокращая:\n\n{sample}",
                ),
            ],
        )
        response = client.chat(chat)

        if hasattr(response, "usage") and response.usage:
            usage = response.usage
            prompt_tokens = getattr(usage, "prompt_tokens", None)
            completion_tokens = getattr(usage, "completion_tokens", None)
            total = getattr(usage, "total_tokens", None)

            print(f"prompt_tokens:     {prompt_tokens}")
            print(f"completion_tokens: {completion_tokens}")
            print(f"total_tokens:      {total}")

            if prompt_tokens:
                ratio = len(sample) / prompt_tokens
                print(f"Символов на токен: {ratio:.2f}")
                print(f"Ожидание документа (3 симв./токен): {len(sample) / 3:.0f}")
                print(f"Реально: {prompt_tokens}")
        else:
            print("Поле usage отсутствует или пустое — токены придётся оценивать приблизительно.")

        # --- Проверка 3: Pydantic-схема в response_format ------------------
        print_section("ПРОВЕРКА 3: Pydantic-схема в response_format")

        try:
            from pydantic import BaseModel, Field
        except ImportError:
            print("Pydantic не установлен. Установи: pip install pydantic")
            return

        class PriceItem(BaseModel):
            name: str = Field(description="Название товара")
            price: str | None = Field(default=None, description="Цена как в документе")

        class PriceListData(BaseModel):
            title: str = Field(description="Название документа")
            vendor: str | None = None
            items: list[PriceItem] = []

        # Генерируем JSON Schema из Pydantic
        schema = PriceListData.model_json_schema()
        print("Pydantic сгенерировал JSON Schema:")
        print(json.dumps(schema, ensure_ascii=False, indent=2))

        sample_price = (
            "Прайс-лист ООО Ромашка на 2026 год.\n"
            "Ноутбук X1 — 89 990 руб.\n"
            "Мышь Y2 — 1 299,50 руб.\n"
        )

        chat = Chat(
            model=model,
            messages=[
                Messages(
                    role=MessagesRole.SYSTEM,
                    content=(
                        "Извлеки данные из прайс-листа по JSON Schema. "
                        "Верни ТОЛЬКО JSON, без пояснений."
                    ),
                ),
                Messages(role=MessagesRole.USER, content=sample_price),
            ],
            response_format={
                "type": "json_schema",
                "schema": schema,
                "strict": True,
            },
        )

        try:
            response = client.chat(chat)
            content = response.choices[0].message.content
            print("ОТВЕТ МОДЕЛИ:")
            print(content)
            print()

            parsed = json.loads(content)
            print("Парсинг JSON: OK")
            print(json.dumps(parsed, ensure_ascii=False, indent=2))

            try:
                validated = PriceListData.model_validate(parsed)
                print()
                print("Валидация Pydantic: OK")
                print(validated)
            except Exception as exc:
                print()
                print(f"Валидация Pydantic: ОШИБКА — {exc}")

        except Exception as exc:
            print(f"ОШИБКА при вызове с Pydantic-схемой: {exc}")
            print()
            print("Скорее всего, GigaChat не принимает схему от Pydantic.")
            print("Тогда нужен либо slim_schema, либо отказ от strict.")


if __name__ == "__main__":
    main()