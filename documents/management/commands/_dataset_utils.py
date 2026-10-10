"""Вспомогательные функции для генерации датасета.

Здесь всё, что относится к форматированию значений, шрифтам,
работе с PDF и DOCX, но не зависит от конкретного типа документа.
"""

from __future__ import annotations

import random
from datetime import date, timedelta
from pathlib import Path

import pymupdf as fitz
from docx import Document as DocxDocument
from docx.shared import Pt


# Шрифт с поддержкой кириллицы для PDF.
CYRILLIC_FONT_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
CYRILLIC_FONT_BOLD_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
CYRILLIC_FONT_NAME = "DejaVu"


def check_font() -> None:
    """Проверяет наличие шрифта с кириллицей."""
    path = Path(CYRILLIC_FONT_PATH)
    if not path.exists():
        raise FileNotFoundError(
            f"Шрифт не найден: {CYRILLIC_FONT_PATH}\n"
            "Установи DejaVu Sans:\n"
            "  sudo apt install fonts-dejavu\n"
            "или найди нужный путь и поправь константу CYRILLIC_FONT_PATH."
        )


# --- Форматирование значений ------------------------------------------------

def format_money(value: int, style: str = "rub_space") -> str:
    """Форматирует число как цену.

    style:
        'rub_space' — «89 990 руб.»
        'rub_sign'  — «89990 ₽»
        'comma'     — «1 299,50»
        'plain'     — «89990»
    """
    if style == "rub_space":
        return f"{value:,}".replace(",", " ") + " руб."
    if style == "rub_sign":
        return f"{value} ₽"
    if style == "comma":
        rub = value
        kop = random.choice([0, 50, 90, 99])
        return f"{rub:,}".replace(",", " ") + f",{kop:02d}"
    if style == "plain":
        return str(value)
    raise ValueError(f"Неизвестный стиль цены: {style}")


def format_memory(value: int, unit: str = "GB", style: str = "ru_space") -> str:
    """Форматирует объём памяти.

    style:
        'ru_space' — «16 ГБ»
        'en_nospace' — «16GB»
        'en_space' — «16 Gb»
        'ru_nospace' — «16ГБ»
    """
    if unit == "GB":
        ru, en = "ГБ", "GB"
    elif unit == "TB":
        ru, en = "ТБ", "TB"
    elif unit == "MB":
        ru, en = "МБ", "MB"
    else:
        ru, en = unit, unit

    if style == "ru_space":
        return f"{value} {ru}"
    if style == "ru_nospace":
        return f"{value}{ru}"
    if style == "en_nospace":
        return f"{value}{en}"
    if style == "en_space":
        return f"{value} {en.capitalize()}"
    raise ValueError(f"Неизвестный стиль памяти: {style}")


def random_money_style() -> str:
    """Случайный стиль цены — для разнообразия датасета."""
    return random.choice(["rub_space", "rub_sign", "comma", "plain"])


def random_memory_style() -> str:
    """Случайный стиль памяти."""
    return random.choice(["ru_space", "ru_nospace", "en_nospace", "en_space"])


def random_date(start: date | None = None, days_back: int = 365) -> date:
    """Случайная дата за последний год."""
    if start is None:
        start = date.today()
    return start - timedelta(days=random.randint(0, days_back))


def format_date_ru(d: date) -> str:
    """Форматирует дату как «15.03.2025»."""
    return d.strftime("%d.%m.%Y")


# --- Генерация DOCX ---------------------------------------------------------

def new_docx() -> DocxDocument:
    """Создаёт новый DOCX с настройками по умолчанию."""
    doc = DocxDocument()
    style = doc.styles["Normal"]
    style.font.name = "DejaVu Sans"
    style.font.size = Pt(11)
    return doc


def add_docx_paragraph(doc: DocxDocument, text: str, bold: bool = False) -> None:
    """Добавляет абзац в DOCX."""
    p = doc.add_paragraph()
    run = p.add_run(text)
    run.bold = bold


def add_docx_table(
    doc: DocxDocument,
    headers: list[str],
    rows: list[list[str]],
) -> None:
    """Добавляет таблицу в DOCX."""
    table = doc.add_table(rows=1, cols=len(headers))
    table.style = "Table Grid"

    for i, header in enumerate(headers):
        cell = table.rows[0].cells[i]
        cell.text = header
        for paragraph in cell.paragraphs:
            for run in paragraph.runs:
                run.bold = True

    for row_data in rows:
        cells = table.add_row().cells
        for i, value in enumerate(row_data):
            cells[i].text = str(value)


# --- Генерация PDF через PyMuPDF --------------------------------------------

def new_pdf_page(width: float = 595, height: float = 842) -> tuple:
    """Создаёт новый PDF-документ и первую страницу (A4).

    Шрифт встраивается не здесь, а при первом insert_text — так PyMuPDF
    делает subset (встраивает только использованные символы), и файл
    получается в разы меньше.
    """
    check_font()
    doc = fitz.open()
    page = doc.new_page(width=width, height=height)
    return doc, page


def pdf_insert_line(
    page,
    y: float,
    text: str,
    x: float = 50,
    fontsize: int = 11,
    bold: bool = False,
) -> float:
    """Вставляет строку в PDF. Возвращает новую Y-координату.

    fontfile передаётся прямо в insert_text, чтобы PyMuPDF сделал subset
    шрифта. Это уменьшает размер PDF с 1.5 МБ до 20-30 КБ.
    """
    if bold and Path(CYRILLIC_FONT_BOLD_PATH).exists():
        fontname = "DejaVu-Bold"
        fontfile = CYRILLIC_FONT_BOLD_PATH
    else:
        fontname = CYRILLIC_FONT_NAME
        fontfile = CYRILLIC_FONT_PATH

    page.insert_text(
        (x, y),
        text,
        fontname=fontname,
        fontfile=fontfile,
        fontsize=fontsize,
    )
    return y + fontsize + 6


def pdf_insert_table(
    page,
    y: float,
    headers: list[str],
    rows: list[list[str]],
    col_widths: list[float] | None = None,
    x: float = 50,
) -> float:
    """Вставляет простую таблицу в PDF текстом."""
    if col_widths is None:
        total_width = 495
        col_widths = [total_width / len(headers)] * len(headers)

    line = ""
    for i, header in enumerate(headers):
        line += header.ljust(int(col_widths[i] / 6)) + "  "
    y = pdf_insert_line(page, y, line.strip(), x=x, bold=True)

    for row in rows:
        line = ""
        for i, value in enumerate(row):
            line += str(value).ljust(int(col_widths[i] / 6)) + "  "
        y = pdf_insert_line(page, y, line.strip(), x=x)

    return y


def save_pdf(doc, path: Path) -> None:
    """Сохраняет PDF с оптимизацией."""
    doc.save(str(path), garbage=4, deflate=True, clean=True)
    doc.close()


def save_pdf_to_bytes(doc) -> bytes:
    """Сохраняет PDF в bytes с subset шрифтов и оптимизацией.

    subset_fonts() удаляет из встроенного шрифта все символы,
    которые не используются в документе. Это уменьшает размер
    с ~700 КБ до ~20-30 КБ.
    """
    from io import BytesIO

    # Делает subset всех встроенных шрифтов.
    # Вызывать ПОСЛЕ того, как все страницы нарисованы.
    try:
        doc.subset_fonts()
    except Exception:
        # subset не критичен — если не сработал, просто продолжаем
        pass

    buf = BytesIO()
    doc.save(buf, garbage=4, deflate=True, clean=True, deflate_fonts=True)
    doc.close()
    return buf.getvalue()