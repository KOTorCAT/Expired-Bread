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

def generate_price_list(seed: int) -> tuple[dict, bytes, bytes]:
    """Генерирует прайс-лист.

    Возвращает (truth, docx_bytes, pdf_bytes).
    """
    rng = random.Random(seed)

    organization = rng.choice(data.ORGANIZATIONS)
    doc_date = u.random_date()

    item_count = rng.randint(6, 12)
    chosen_items = rng.sample(data.PRICE_ITEMS, item_count)

    truth_items = []
    rows_for_doc = []

    for name, unit, base_price in chosen_items:
        price_value = base_price + rng.randint(-500, 500)
        price_value = max(price_value, 100)

        style = u.random_money_style()
        price_str = u.format_money(price_value, style)

        truth_items.append({
            "name": name,
            "sku": f"ART-{rng.randint(1000, 9999)}",
            "price": price_str,
            "unit": unit,
        })
        rows_for_doc.append([name, unit, price_str])

    truth = {
        "title": f"Прайс-лист {organization}",
        "description": f"Прайс-лист на {u.format_date_ru(doc_date)}",
        "organization": organization,
        "date": u.format_date_ru(doc_date),
        "vendor": organization,
        "currency": "RUB",
        "items": truth_items,
    }

    docx_bytes = _render_price_list_docx(organization, doc_date, rows_for_doc, seed)
    pdf_bytes = _render_price_list_pdf(organization, doc_date, rows_for_doc, seed)

    return truth, docx_bytes, pdf_bytes


def _render_price_list_docx(
    organization: str,
    doc_date: date,
    rows: list[list[str]],
    seed: int,
) -> bytes:
    rng = random.Random(seed + 1)
    doc = u.new_docx()

    u.add_docx_paragraph(doc, organization, bold=True)
    u.add_docx_paragraph(doc, f"Дата: {u.format_date_ru(doc_date)}")
    doc.add_paragraph()

    variant = rng.choice([0, 1, 2])

    if variant == 0:
        u.add_docx_table(doc, ["Наименование", "Ед.", "Цена"], rows)
    elif variant == 1:
        for name, unit, price in rows:
            doc.add_paragraph(f"{name} — {price} за {unit}", style="List Bullet")
    else:
        u.add_docx_paragraph(doc, "Перечень товаров:", bold=True)
        u.add_docx_table(doc, ["Наименование", "Ед.", "Цена"], rows)

    buf = BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _render_price_list_pdf(
    organization: str,
    doc_date: date,
    rows: list[list[str]],
    seed: int,
) -> bytes:
    rng = random.Random(seed + 2)
    doc, page = u.new_pdf_page()
    y = 60

    y = u.pdf_insert_line(page, y, organization, bold=True, fontsize=13)
    y = u.pdf_insert_line(page, y, f"Дата: {u.format_date_ru(doc_date)}")
    y += 10

    variant = rng.choice([0, 1, 2])

    if variant == 0:
        y = u.pdf_insert_table(
            page, y,
            ["Наименование", "Ед.", "Цена"],
            rows,
            col_widths=[300, 60, 135],
        )
    elif variant == 1:
        for name, unit, price in rows:
            y = u.pdf_insert_line(page, y, f"• {name} — {price} за {unit}")
    else:
        y = u.pdf_insert_line(page, y, "Перечень товаров:", bold=True)
        y += 5
        y = u.pdf_insert_table(
            page, y,
            ["Наименование", "Ед.", "Цена"],
            rows,
            col_widths=[300, 60, 135],
        )

    return u.save_pdf_to_bytes(doc)


# --- Спецификация -----------------------------------------------------------

def generate_specification(seed: int) -> tuple[dict, bytes, bytes]:
    """Генерирует спецификацию на одно изделие."""
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

    docx_bytes = _render_specification_docx(truth, seed)
    pdf_bytes = _render_specification_pdf(truth, seed)

    return truth, docx_bytes, pdf_bytes


def _render_specification_docx(truth: dict, seed: int) -> bytes:
    rng = random.Random(seed + 1)
    doc = u.new_docx()

    u.add_docx_paragraph(doc, truth["organization"], bold=True)
    u.add_docx_paragraph(doc, f"Дата: {truth['date']}")
    doc.add_paragraph()
    u.add_docx_paragraph(doc, f"Спецификация: {truth['product_name']}", bold=True)
    doc.add_paragraph()

    variant = rng.choice([0, 1])

    if variant == 0:
        rows = [
            ["Производитель", truth["manufacturer"]],
            ["Категория", truth["category"]],
            ["Цена", truth["price"]],
            ["Оперативная память", truth["ram"]],
            ["Накопитель", truth["storage"]],
        ]
        for key, value in truth["characteristics"].items():
            rows.append([key, value])
        u.add_docx_table(doc, ["Характеристика", "Значение"], rows)
    else:
        doc.add_paragraph(f"Производитель: {truth['manufacturer']}")
        doc.add_paragraph(f"Категория: {truth['category']}")
        doc.add_paragraph(f"Цена: {truth['price']}")
        doc.add_paragraph(f"Оперативная память: {truth['ram']}")
        doc.add_paragraph(f"Накопитель: {truth['storage']}")
        for key, value in truth["characteristics"].items():
            doc.add_paragraph(f"{key}: {value}")

    buf = BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _render_specification_pdf(truth: dict, seed: int) -> bytes:
    rng = random.Random(seed + 2)
    doc, page = u.new_pdf_page()
    y = 60

    y = u.pdf_insert_line(page, y, truth["organization"], bold=True, fontsize=13)
    y = u.pdf_insert_line(page, y, f"Дата: {truth['date']}")
    y += 10
    y = u.pdf_insert_line(page, y, f"Спецификация: {truth['product_name']}", bold=True, fontsize=12)
    y += 10

    variant = rng.choice([0, 1])

    if variant == 0:
        rows = [
            ["Производитель", truth["manufacturer"]],
            ["Категория", truth["category"]],
            ["Цена", truth["price"]],
            ["Оперативная память", truth["ram"]],
            ["Накопитель", truth["storage"]],
        ]
        for key, value in truth["characteristics"].items():
            rows.append([key, value])
        y = u.pdf_insert_table(
            page, y,
            ["Характеристика", "Значение"],
            rows,
            col_widths=[200, 295],
        )
    else:
        y = u.pdf_insert_line(page, y, f"Производитель: {truth['manufacturer']}")
        y = u.pdf_insert_line(page, y, f"Категория: {truth['category']}")
        y = u.pdf_insert_line(page, y, f"Цена: {truth['price']}")
        y = u.pdf_insert_line(page, y, f"Оперативная память: {truth['ram']}")
        y = u.pdf_insert_line(page, y, f"Накопитель: {truth['storage']}")
        for key, value in truth["characteristics"].items():
            y = u.pdf_insert_line(page, y, f"{key}: {value}")

    return u.save_pdf_to_bytes(doc)


# --- Техническая документация -----------------------------------------------

def generate_technical_document(seed: int) -> tuple[dict, bytes, bytes]:
    """Генерирует техническую документацию."""
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
        sections.append({"title": title, "content": content})

    requirements = rng.sample(data.TECH_REQUIREMENTS, rng.randint(2, 4))

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