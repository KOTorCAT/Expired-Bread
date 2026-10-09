# Expired Bread

Веб-система для хранения электронных документов с AI-анализом содержимого.
Пользователь загружает PDF или DOCX, система извлекает текст, анализирует его
через GigaChat, сохраняет структурированные данные и предоставляет:

- поиск по метаданным и по содержимому файлов;
- ответы на вопросы по документам на естественном языке;
- ручную проверку и редактирование результата AI.

Проект разрабатывается как модульный монолит на Django. Каждый модуль
(извлечение текста, работа с LLM, поиск, Q&A) изолирован и тестируется
отдельно.

---

## Статус проекта

Реализовано ядро MVP. Осталось: ZIP-архивы, авторизация с ролями и лимитами,
финальная полировка интерфейса, проверка запуска из чистого окружения.

**Тесты:** 34 теста, все проходят.

---

## Технологический стек

| Компонент        | Решение                              |
| ---------------- | ------------------------------------ |
| Язык             | Python 3.11+                         |
| Backend          | Django 5.1                           |
| База данных      | SQLite                               |
| Интерфейс        | HTML + CSS + немного JS              |
| Файлы            | Django FileField + локальная ФС      |
| AI-провайдер     | GigaChat (модель `GigaChat-2-Max`)   |
| PDF              | PyMuPDF                              |
| DOCX             | python-docx                          |
| Конфигурация     | python-dotenv, `.env`                |
| Тесты            | Django TestCase                      |

---

## Что реализовано

### 1. Загрузка и хранение документов

- Загрузка PDF и DOCX через веб-форму.
- Проверка расширения (`.pdf`, `.docx`) и размера файла (до 20 МБ).
- Сохранение оригинала в `media/documents/YYYY/MM/`.
- Модель `Document` со статусами: `uploaded`, `processing`, `review`,
  `ready`, `failed`.
- Список документов, детальная страница, скачивание оригинала.

### 2. Извлечение текста

Модуль `documents/services/extractors.py`:

- `extract_text(file_path, file_type)` — диспетчер по расширению.
- `extract_pdf_text(file_path)` — извлечение через PyMuPDF.
- `extract_docx_text(file_path)` — извлечение через python-docx, включая таблицы.
- Явное исключение `ExtractionError`, если текста нет (скан-PDF без OCR).

### 3. AI-обработка

Модуль `documents/services/llm.py`:

- `analyze_document(text)` — текст → структурированный JSON через GigaChat.
- `analyze_document_vision(file_path)` — запасной путь для сканов: файл
  загружается через Files API и передаётся в модель как вложение.
- `answer_question(question, documents_context)` — Q&A по контексту.
- `validate_document_data(data)` — проверка структуры ответа.
- Ретраи с backoff при сетевых ошибках, таймауты, очистка служебных ключей
  (`../../../`, `_comment_*`, `__*`).
- JSON Schema (`response_format`) для строгого формата ответа.
- API-ключ читается из `.env`, в код не попадает.

### 4. Оркестрация обработки

Модуль `documents/services/document_service.py`:

- `process_document(document_id)` — полный цикл обработки документа:
  извлечение текста → LLM → сохранение `data` → статус `review` или `failed`.
- `confirm_document(document_id, cleaned_data)` — сохранение подтверждённых
  пользователем данных, статус `ready`.
- При ошибке LLM оригинал сохраняется, статус `failed`, текст ошибки в
  `error_message`. Кнопка «Повторить обработку» на детальной странице.

### 5. Проверка пользователем

- Форма `DocumentReviewForm` с полями: название, тип, описание.
- Отдельная страница `/documents/<id>/review/`.
- Полный ответ AI (JSON) показан под формой.
- После подтверждения документ становится доступен в поиске.

### 6. Поиск

Модуль `documents/services/search_service.py`:

- Поиск по `title`, `description`, `original_filename`, `extracted_text`.
- Фильтр по типу документа.
- По умолчанию ищет только среди `ready`-документов.
- Белый список допустимых значений — защита от произвольных GET-параметров.
- `parse_search_query()` — заглушка под будущий LLM-парсинг запроса.

Модуль `documents/services/normalization.py`:

- `build_search_text(...)` — собирает нормализованный текст (нижний регистр)
  для поиска. Нужно, потому что SQLite `LIKE` регистронезависим только для
  ASCII, а для кириллицы — нет.
- `normalize_query(query)` — приведение запроса к нижнему регистру.

### 7. Q&A по содержимому документов

Модуль `documents/services/qa_service.py`:

- `ask(question)` — принимает вопрос, ищет релевантные документы
  (top-3, по словам вопроса), собирает контекст (до 3000 символов на
  документ), отправляет в GigaChat.
- Возвращает `{found, answer, sources}`.
- Если релевантных документов не нашлось — в LLM не отправляется,
  сразу ответ «в базе нет документов». Гарантия того, что система не
  выдумывает факты.
- Отдельная страница `/documents/ask/`.

### 8. Management-команда

- `python manage.py backfill_extracted_text` — заполняет `extracted_text`
  и `search_text` для существующих документов. Полезна после изменений
  схемы.

### 9. Тесты

- `test_extractors.py` — 6 тестов: PDF, DOCX, диспетчер, ошибки.
- `test_llm.py` — 11 тестов: валидация, парсинг, очистка ключей.
- `test_processing.py` — 3 теста: полный цикл, ошибка LLM, fallback на vision.
- `test_search.py` — 9 тестов: поиск по полям, фильтры, регистронезависимость.
- `test_qa.py` — 5 тестов: happy path, отсутствие документов, пустой вопрос.

---

## Что осталось сделать

### Обязательное (по техзаданию MVP)

- **ZIP-архивы.** Модель `DocumentArchive`, сервис `create_archive()`,
  выбор документов чекбоксами на странице списка, скачивание архива.
- **Проверка запуска из чистого окружения.** Клонирование в новую папку,
  `pip install -r requirements.txt`, миграции, запуск.
- **README и документация.** Этот файл.

### Расширения (после MVP)

- **Авторизация и роли.** Django auth, привязка `Document.user`,
  `@login_required`, изоляция данных между пользователями.
- **Роли и лимиты.** admin без ограничений, premium и user с лимитами
  на количество файлов, размер хранилища и число запросов к AI.
- **Фронтенд.** Спиннеры для долгих операций, стилизация форм и таблиц,
  уведомления, автообновление статуса `processing`.
- **Удаление документов.** Сейчас возможно только через `/admin/`.
- **Редактирование после `ready`.** Сейчас форма доступна только в статусе
  `review`.
- **Редактирование поля `data` (JSON).** Сейчас только через админку.
- **Streaming ответа GigaChat.** Отображение ответа по частям, как в ChatGPT.
- **Фоновая обработка.** Celery + Redis для больших документов.
- **SQLite FTS5.** Полнотекстовый индекс с ранжированием.
- **Embeddings и семантический поиск.** Векторный поиск через модели
  `Embeddings` / `EmbeddingsGigaR`.
- **OCR для сканов.** Пока сканы без текстового слоя обрабатываются через
  Vision, но текст не сохраняется в `extracted_text`.
- **Поддержка изображений.** JPEG / PNG через ту же Vision-модель.
- **PostgreSQL.** Вместо SQLite для масштабирования.
- **Деплой.** VPS + Gunicorn + Nginx + Let's Encrypt.

---

## Архитектура

```
Браузер
   ↓
Django views (documents/views.py)
   ↓
Слой сервисов (documents/services/)
   ├── extractors.py       PDF/DOCX → текст
   ├── llm.py              текст → JSON, Q&A
   ├── document_service.py оркестрация обработки
   ├── search_service.py   поиск по БД
   ├── qa_service.py       Q&A
   └── normalization.py    нормализация текста
   ↓
Модели (documents/models.py)
   ↓
SQLite + файловая система
```

**Принципы:**

- `views.py` вызывает сервисы, не содержит бизнес-логики.
- Сервисы не знают про HTTP и шаблоны.
- `extractors.py` не знает про Django.
- `llm.py` не работает с БД и файловой системой.
- Модели описывают данные, а не управляют процессом.

---

## Установка

### Требования

- Python 3.11+
- pip
- git

### Шаги

```bash
git clone <URL-репозитория>
cd Expired-Bread

python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\Activate.ps1

pip install -r requirements.txt
```

### Настройка окружения

1. Скопируй шаблон:

```bash
cp .env.example .env
```

2. Заполни `.env`:

```
GIGACHAT_CREDENTIALS=твой_ключ_авторизации
GIGACHAT_SCOPE=GIGACHAT_API_PERS
GIGACHAT_MODEL=GigaChat-2-Max
GIGACHAT_CA_BUNDLE_FILE=certs/russian_trusted_root_ca_pem.crt
GIGACHAT_VERIFY_SSL_CERTS=True
```

3. Скачай сертификат Минцифры (обязательно для GigaChat):

```bash
mkdir -p certs
curl -k "https://gu-st.ru/content/lending/russian_trusted_root_ca_pem.crt" \
  -o certs/russian_trusted_root_ca_pem.crt
```

4. Примени миграции:

```bash
python manage.py migrate
```

5. (Опционально) Создай суперпользователя:

```bash
python manage.py createsuperuser
```

6. Запусти сервер:

```bash
python manage.py runserver
```

Открой http://127.0.0.1:8000/

---

## Структура проекта

```
expired_bread/
├── manage.py
├── requirements.txt
├── .env.example
├── .gitignore
├── README.md
│
├── config/
│   ├── settings.py
│   ├── urls.py
│   ├── asgi.py
│   └── wsgi.py
│
├── documents/
│   ├── models.py                 # Document
│   ├── forms.py                  # DocumentUploadForm, DocumentReviewForm
│   ├── views.py                  # index, list, upload, detail, review,
│   │                             # reprocess, search, ask
│   ├── urls.py
│   ├── admin.py
│   │
│   ├── services/
│   │   ├── extractors.py         # PDF/DOCX → текст
│   │   ├── llm.py                # GigaChat: анализ и Q&A
│   │   ├── document_service.py   # оркестрация
│   │   ├── search_service.py     # поиск
│   │   ├── qa_service.py         # Q&A
│   │   └── normalization.py      # нормализация текста
│   │
│   ├── management/commands/
│   │   └── backfill_extracted_text.py
│   │
│   ├── templates/documents/
│   │   ├── index.html
│   │   ├── document_list.html
│   │   ├── document_upload.html
│   │   ├── document_detail.html
│   │   ├── document_review.html
│   │   ├── search_results.html
│   │   └── ask.html
│   │
│   ├── static/documents/
│   │   ├── css/style.css
│   │   └── js/app.js
│   │
│   ├── migrations/
│   └── tests/
│       ├── test_extractors.py
│       ├── test_llm.py
│       ├── test_processing.py
│       ├── test_search.py
│       └── test_qa.py
│
├── templates/
│   └── base.html
│
├── media/                        # локальные файлы (не в Git)
│   ├── documents/
│   └── archives/
│
└── certs/                        # сертификат Минцифры (не в Git)
```

---

## Модель данных

### `Document`

| Поле                | Тип              | Назначение                              |
| ------------------- | ---------------- | --------------------------------------- |
| `id`                | AutoField        | Уникальный ID                           |
| `file`              | FileField        | Путь к оригиналу                        |
| `original_filename` | CharField        | Исходное имя файла                      |
| `document_type`     | CharField        | Тип (`resume`, `price_list`, `report`…) |
| `title`             | CharField        | Название от AI или пользователя         |
| `description`       | TextField        | Краткое описание                        |
| `data`              | JSONField        | Полный структурированный ответ AI       |
| `extracted_text`    | TextField        | Извлечённый текст (для поиска)          |
| `search_text`       | TextField        | Нормализованный текст (нижний регистр)  |
| `status`            | CharField        | `uploaded` / `processing` / `review` /  |
|                     |                  | `ready` / `failed`                      |
| `error_message`     | TextField        | Текст ошибки обработки                  |
| `created_at`        | DateTimeField    | Время загрузки                          |
| `updated_at`        | DateTimeField    | Время последнего изменения              |

### Статусы обработки

- `uploaded` — файл загружен.
- `processing` — идёт обработка.
- `review` — AI вернул результат, требуется проверка.
- `ready` — пользователь подтвердил данные.
- `failed` — обработка завершилась ошибкой.

---

## Сценарий работы

1. Пользователь загружает PDF или DOCX.
2. `forms.py` проверяет расширение и размер.
3. `views.py` сохраняет `Document` и файл.
4. `document_service.process_document()`:
   - `extractors.extract_text()` → текст;
   - сохраняет текст в `extracted_text`;
   - `llm.analyze_document()` → JSON;
   - сохраняет JSON в `data`;
   - статус → `review`.
5. Пользователь открывает форму review, правит поля, подтверждает.
6. `confirm_document()` сохраняет правки, пересчитывает `search_text`,
   ставит статус `ready`.
7. Документ доступен в поиске и в Q&A.

При ошибке на любом шаге:

- оригинал сохраняется;
- статус → `failed`;
- текст ошибки в `error_message`;
- пользователь видит кнопку «Повторить обработку».

---

## Демонстрационный сценарий

1. **Загрузка.** Загрузить резюме в PDF.
2. **Обработка.** Система извлекает текст и формирует JSON с именем,
   навыками, опытом, образованием.
3. **Проверка.** Исправить неточность, если есть, подтвердить.
4. **Каталог.** Документ появился в списке со статусом `ready`.
5. **Поиск.** Ввести `Python` — найдётся резюме.
6. **Вопрос.** Задать вопрос «Какой стек технологий указан в резюме?» —
   получить ответ со списком технологий и источником.
7. **Проверка отсутствия.** Задать вопрос про несуществующие сведения
   (например, «Какие сертификаты Kubernetes?») — система ответит,
   что данных нет.
8. **Дополнительный тип.** Загрузить прайс-лист или отчёт — убедиться,
   что система не ограничена резюме.
9. **Архивирование.** Выбрать несколько документов, скачать ZIP.

Пункты 1–8 работают. Пункт 9 — в разработке.

---

## Тесты

Запуск всех тестов:

```bash
python manage.py test documents.tests -v 2
```

Ожидаемо: **34 теста, все `ok`**.

Запуск отдельного модуля:

```bash
python manage.py test documents.tests.test_qa -v 2
```

Покрытие:

- извлечение текста из PDF и DOCX;
- валидация и парсинг ответа LLM;
- очистка ключей от служебных префиксов;
- полный цикл обработки документа;
- fallback на Vision при сканах;
- поиск по метаданным и содержимому;
- регистронезависимость для кириллицы;
- Q&A с источниками и без;
- отказ отвечать при отсутствии документов.

---

## Безопасность и конфиденциальность

- API-ключи и `SECRET_KEY` хранятся в `.env`, не в коде.
- `.env` и `certs/` не попадают в Git.
- Перед отправкой текста в GigaChat учитывайте: данные передаются
  провайдеру API. Для демонстрации используйте синтетические или
  обезличенные документы.
- Полный текст документов не сохраняется в логи.
- При создании архивов оригиналы не удаляются — архив это выгрузка,
  а не единственная копия.

---

## Ограничения текущей версии

- **Сканы.** PDF без текстового слоя обрабатываются через Vision, но текст
  в `extracted_text` не сохраняется. Поиск по содержимому для таких
  документов не работает.
- **Кириллица в поиске.** Регистронезависимый поиск работает, но есть
  редкий случай, когда PDF содержит латинские символы в кириллических
  словах (`c` вместо `с`). Известная проблема, будет решаться через FTS5
  или дополнительные нормализаторы.
- **Поиск через LLM.** Сейчас поиск по ключевым словам, не семантический.
  LLM в поиске участвует только в Q&A.
- **Синхронная обработка.** Загрузка может занимать 5–15 секунд, пока
  GigaChat обрабатывает документ. Решается через Celery.

---





