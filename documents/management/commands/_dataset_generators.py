"""Генераторы документов для датасета.

Каждая функция создаёт один документ: эталон (truth), DOCX и PDF.
Все данные вымышленные.
"""

from __future__ import annotations

import random
from datetime import date
from io import BytesIO

from documents.management.commands import _dataset_data as data
from documents.management.commands import _dataset_utils as u


# --- Прайс-лист -------------------------------------------------------------

def generate_price_list(
    seed: int,
    *,
    hard: bool = False,
    long: bool = False,
) -> tuple[dict, bytes, bytes]:
    """Генерирует прайс-лист.

    hard — документ «сложный»: у позиций нет единицы измерения,
    подписи колонок заменены синонимами, есть шум (служебные
    надписи, которых нет в эталоне).
    long — длинный прайс на 45–60 позиций вместо 6–12.

    Возвращает (truth, docx_bytes, pdf_bytes).
    """
    # Стили цен и копейки выбираются через глобальный random,
    # поэтому сеем и его: иначе одинаковый seed даёт разные документы
    # и датасет не воспроизводится.
    random.seed(seed)
    rng = random.Random(seed)

    organization = rng.choice(data.ORGANIZATIONS)
    doc_date = u.random_date()

    if long:
        item_count = rng.randint(45, 60)
        # Для длинного прайса позиции берём с повторами: каталог
        # товаров короче, а длина документа важнее уникальности.
        chosen_items = rng.choices(data.PRICE_ITEMS, k=item_count)
    else:
        item_count = rng.randint(6, 12)
        chosen_items = rng.sample(data.PRICE_ITEMS, item_count)

    truth_items = []
    rows_for_doc = []

    for name, unit, base_price in chosen_items:
        price_value = base_price + rng.randint(-500, 500)
        price_value = max(price_value, 100)

        style = u.random_money_style()
        price_str = u.format_money(price_value, style)

        sku = f"ART-{rng.randint(1000, 9999)}"

        item = {"name": name, "sku": sku, "price": price_str}
        if not hard:
            # В сложном документе колонки единиц измерения нет —
            # и в эталоне её тоже быть не должно.
            item["unit"] = unit
        truth_items.append(item)
        rows_for_doc.append([name, sku, unit, price_str])

    truth = {
        "title": f"Прайс-лист {organization}",
        "description": f"Прайс-лист на {u.format_date_ru(doc_date)}",
        "organization": organization,
        "date": u.format_date_ru(doc_date),
        "vendor": organization,
        "currency": "RUB",
        "items": truth_items,
    }

    docx_bytes = _render_price_list_docx(
        truth["title"], organization, doc_date, rows_for_doc, seed, hard=hard
    )
    pdf_bytes = _render_price_list_pdf(
        truth["title"], organization, doc_date, rows_for_doc, seed, hard=hard
    )

    return truth, docx_bytes, pdf_bytes


def _render_price_list_docx(
    title: str,
    organization: str,
    doc_date: date,
    rows: list[list[str]],
    seed: int,
    *,
    hard: bool = False,
) -> bytes:
    rng = random.Random(seed + 1)
    doc = u.new_docx()

    # Заголовок печатаем дословно: он же записан в эталоне как title.
    u.add_docx_paragraph(doc, title, bold=True)
    u.add_docx_paragraph(doc, f"Дата: {u.format_date_ru(doc_date)}")
    doc.add_paragraph()

    variant = rng.choice([0, 1, 2])

    if hard:
        headers = ["Товар", "Артикул", "Стоимость, ₽"]
        u.add_docx_paragraph(doc, "Конфиденциально. Внутренний документ.")
        # В сложном документе колонки единиц нет — приводим строки
        # к тому же числу колонок, что и заголовки.
        rows = [[row[0], row[1], row[-1]] for row in rows]
    else:
        headers = ["Наименование", "Артикул", "Ед.", "Цена"]

    if variant == 0:
        u.add_docx_table(doc, headers, rows)
    elif variant == 1:
        for row in rows:
            unit = row[2] if len(row) == 4 else ""
            suffix = f" за {unit}" if unit else ""
            doc.add_paragraph(
                f"{row[0]}, артикул {row[1]} — {row[-1]}{suffix}",
                style="List Bullet",
            )
    else:
        u.add_docx_paragraph(doc, "Перечень товаров:", bold=True)
        u.add_docx_table(doc, headers, rows)

    buf = BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _render_price_list_pdf(
    title: str,
    organization: str,
    doc_date: date,
    rows: list[list[str]],
    seed: int,
    *,
    hard: bool = False,
) -> bytes:
    rng = random.Random(seed + 2)
    doc, page = u.new_pdf_page()
    y = 60

    y = u.pdf_insert_line(page, y, title, bold=True, fontsize=13)
    y = u.pdf_insert_line(page, y, f"Дата: {u.format_date_ru(doc_date)}")
    y += 10

    variant = rng.choice([0, 1, 2])

    if hard:
        headers = ["Товар", "Артикул", "Стоимость, ₽"]
        widths = [280, 110, 105]
        y = u.pdf_insert_line(
            page, y, "Конфиденциально. Внутренний документ."
        )
        rows = [[row[0], row[1], row[-1]] for row in rows]
    else:
        headers = ["Наименование", "Артикул", "Ед.", "Цена"]
        widths = [230, 95, 55, 115]

    if variant == 0:
        y = u.pdf_insert_table(page, y, headers, rows, col_widths=widths)
    elif variant == 1:
        for row in rows:
            unit = row[2] if len(row) == 4 else ""
            suffix = f" за {unit}" if unit else ""
            y = u.pdf_insert_line(
                page, y, f"• {row[0]}, артикул {row[1]} — {row[-1]}{suffix}"
            )
    else:
        y = u.pdf_insert_line(page, y, "Перечень товаров:", bold=True)
        y += 5
        y = u.pdf_insert_table(page, y, headers, rows, col_widths=widths)

    return u.save_pdf_to_bytes(doc)


# --- Спецификация -----------------------------------------------------------

def generate_specification(
    seed: int,
    *,
    hard: bool = False,
    long: bool = False,
) -> tuple[dict, bytes, bytes]:
    """Генерирует спецификацию на одно изделие.

    hard — нет накопителя и цвета, подписи заменены синонимами
    («ОЗУ», «Диск», «Стоимость»).
    long — 60 дополнительных характеристик, документ на 3–5 тыс.
    символов вместо одной тысячи.
    """
    # Стили цен и копейки выбираются через глобальный random,
    # поэтому сеем и его: иначе одинаковый seed даёт разные документы
    # и датасет не воспроизводится.
    random.seed(seed)
    rng = random.Random(seed)

    organization = rng.choice(data.ORGANIZATIONS)
    manufacturer = rng.choice(data.MANUFACTURERS)
    category = rng.choice(data.CATEGORIES)

    if category == "Ноутбуки":
        model = rng.choice(data.LAPTOP_MODELS)
    elif category == "Смартфоны":
        model = rng.choice(data.SMARTPHONE_MODELS)
    else:
        model = f"Модель-{rng.randint(100, 999)}"

    product_name = f"{manufacturer} {model}"
    doc_date = u.random_date()

    ram_value = rng.choice([8, 16, 32, 64])
    ram_str = u.format_memory(ram_value, "GB", u.random_memory_style())

    storage_value = rng.choice([256, 512, 1024, 2048])
    storage_unit = "GB" if storage_value < 1024 else "TB"
    storage_value_norm = storage_value if storage_value < 1024 else storage_value // 1024
    storage_str = u.format_memory(storage_value_norm, storage_unit, u.random_memory_style())

    price_value = rng.randint(30000, 200000)
    price_str = u.format_money(price_value, u.random_money_style())

    truth = {
        "title": f"Спецификация {product_name}",
        "description": f"Технические характеристики {product_name}",
        "organization": organization,
        "date": u.format_date_ru(doc_date),
        "product_name": product_name,
        "manufacturer": manufacturer,
        "category": category,
        "price": price_str,
        "ram": ram_str,
        "storage": storage_str,
        "characteristics": {
            "Процессор": f"Intel Core i{rng.randint(5, 9)}",
            "Диагональ экрана": f"{rng.choice([13, 14, 15, 16, 17])}\"",
            "Цвет": rng.choice(["чёрный", "серебристый", "серый", "белый"]),
        },
    }

    if hard:
        # Поля, которых в документе не будет, убираем и из эталона:
        # иначе модель обязана «угадать» отсутствующее значение.
        truth.pop("storage", None)
        truth["characteristics"].pop("Цвет", None)

    if long:
        for index in range(1, 61):
            truth["characteristics"][f"Дополнительный параметр {index}"] = (
                f"значение {index}, серия {rng.randint(1000, 9999)}"
            )

    docx_bytes = _render_specification_docx(truth, seed, hard=hard)
    pdf_bytes = _render_specification_pdf(truth, seed, hard=hard)

    return truth, docx_bytes, pdf_bytes


# Подписи полей в документе: обычные и «сложные» (синонимы).
_SPEC_LABELS = {
    "manufacturer": "Производитель",
    "category": "Категория",
    "price": "Цена",
    "ram": "Оперативная память",
    "storage": "Накопитель",
}

_SPEC_LABELS_HARD = {
    "manufacturer": "Изготовитель",
    "category": "Тип",
    "price": "Стоимость",
    "ram": "ОЗУ",
    "storage": "Диск",
}


def _spec_rows(truth: dict, *, hard: bool = False) -> list[list[str]]:
    """Строки таблицы спецификации: только те поля, что есть в эталоне."""
    labels = _SPEC_LABELS_HARD if hard else _SPEC_LABELS
    rows = []
    for key in ("manufacturer", "category", "price", "ram", "storage"):
        if key in truth:
            rows.append([labels[key], truth[key]])
    for key, value in truth["characteristics"].items():
        rows.append([key, value])
    return rows


def _render_specification_docx(
    truth: dict, seed: int, *, hard: bool = False
) -> bytes:
    rng = random.Random(seed + 1)
    doc = u.new_docx()

    u.add_docx_paragraph(doc, truth["organization"], bold=True)
    u.add_docx_paragraph(doc, f"Дата: {truth['date']}")
    doc.add_paragraph()
    u.add_docx_paragraph(doc, truth["title"], bold=True)
    doc.add_paragraph()

    if hard:
        u.add_docx_paragraph(doc, "Документ предназначен для внутреннего использования.")

    variant = rng.choice([0, 1])

    if variant == 0:
        u.add_docx_table(doc, ["Характеристика", "Значение"], _spec_rows(truth, hard=hard))
    else:
        for label, value in _spec_rows(truth, hard=hard):
            doc.add_paragraph(f"{label}: {value}")

    buf = BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _render_specification_pdf(
    truth: dict, seed: int, *, hard: bool = False
) -> bytes:
    rng = random.Random(seed + 2)
    doc, page = u.new_pdf_page()
    y = 60

    y = u.pdf_insert_line(page, y, truth["organization"], bold=True, fontsize=13)
    y = u.pdf_insert_line(page, y, f"Дата: {truth['date']}")
    y += 10
    y = u.pdf_insert_line(page, y, truth["title"], bold=True, fontsize=12)
    y += 10

    if hard:
        y = u.pdf_insert_line(
            page, y, "Документ предназначен для внутреннего использования."
        )

    variant = rng.choice([0, 1])
    rows = _spec_rows(truth, hard=hard)

    if variant == 0:
        y = u.pdf_insert_table(
            page, y,
            ["Характеристика", "Значение"],
            rows,
            col_widths=[200, 295],
        )
    else:
        for label, value in rows:
            y = u.pdf_insert_line(page, y, f"{label}: {value}")

    return u.save_pdf_to_bytes(doc)


# --- Техническая документация -----------------------------------------------

def generate_technical_document(
    seed: int,
    *,
    hard: bool = False,
    long: bool = False,
) -> tuple[dict, bytes, bytes]:
    """Генерирует техническую документацию.

    hard — нет блока требований, есть служебные надписи.
    long — разделы с длинным текстом, документ на 3–5 тыс. символов.
    """
    # Стили цен и копейки выбираются через глобальный random,
    # поэтому сеем и его: иначе одинаковый seed даёт разные документы
    # и датасет не воспроизводится.
    random.seed(seed)
    rng = random.Random(seed)

    organization = rng.choice(data.ORGANIZATIONS)
    doc_title = rng.choice(data.TECH_DOC_TITLES)
    doc_code = rng.choice(data.TECH_DOC_CODES)
    doc_date = u.random_date()
    version = f"{rng.randint(1, 3)}.{rng.randint(0, 9)}"

    section_count = rng.randint(2, 4)
    section_titles = [
        "Назначение",
        "Технические характеристики",
        "Условия эксплуатации",
        "Комплектация",
        "Меры безопасности",
    ]
    chosen_sections = rng.sample(section_titles, section_count)

    sections = []
    for title in chosen_sections:
        content = f"Раздел «{title}» содержит описание по стандарту {doc_code}."
        if long:
            body = " ".join(
                f"Пункт {index}: требования по стандарту {doc_code}."
                for index in range(1, 13)
            )
            content = f"{content} {body}"
        sections.append({"title": title, "content": content})

    requirements = rng.sample(data.TECH_REQUIREMENTS, rng.randint(2, 4))
    if hard:
        # В сложном документе блока требований нет — и в эталоне тоже.
        requirements = []

    truth = {
        "title": f"{doc_title} {doc_code}",
        "description": f"Техническая документация на изделие, версия {version}",
        "organization": organization,
        "date": u.format_date_ru(doc_date),
        "document_code": doc_code,
        "version": version,
        "sections": sections,
        "requirements": requirements,
    }

    docx_bytes = _render_technical_docx(truth, seed)
    pdf_bytes = _render_technical_pdf(truth, seed)

    return truth, docx_bytes, pdf_bytes


def _render_technical_docx(truth: dict, seed: int) -> bytes:
    doc = u.new_docx()

    u.add_docx_paragraph(doc, truth["organization"], bold=True)
    u.add_docx_paragraph(doc, f"Дата: {truth['date']}")
    u.add_docx_paragraph(doc, f"Обозначение: {truth['document_code']}")
    u.add_docx_paragraph(doc, f"Версия: {truth['version']}")
    doc.add_paragraph()

    u.add_docx_paragraph(doc, truth["title"], bold=True)
    doc.add_paragraph()

    for section in truth["sections"]:
        u.add_docx_paragraph(doc, section["title"], bold=True)
        doc.add_paragraph(section["content"])

    if truth["requirements"]:
        u.add_docx_paragraph(doc, "Требования:", bold=True)
        for req in truth["requirements"]:
            doc.add_paragraph(req, style="List Bullet")

    buf = BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _render_technical_pdf(truth: dict, seed: int) -> bytes:
    doc, page = u.new_pdf_page()
    y = 60

    y = u.pdf_insert_line(page, y, truth["organization"], bold=True, fontsize=13)
    y = u.pdf_insert_line(page, y, f"Дата: {truth['date']}")
    y = u.pdf_insert_line(page, y, f"Обозначение: {truth['document_code']}")
    y = u.pdf_insert_line(page, y, f"Версия: {truth['version']}")
    y += 10
    y = u.pdf_insert_line(page, y, truth["title"], bold=True, fontsize=12)
    y += 10

    for section in truth["sections"]:
        y = u.pdf_insert_line(page, y, section["title"], bold=True)
        y = u.pdf_insert_line(page, y, section["content"])
        y += 5

    if truth["requirements"]:
        y = u.pdf_insert_line(page, y, "Требования:", bold=True)
        for req in truth["requirements"]:
            y = u.pdf_insert_line(page, y, f"• {req}")

    return u.save_pdf_to_bytes(doc)