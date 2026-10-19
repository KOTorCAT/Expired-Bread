"""Проверка контраста палитры по WCAG.

Цвета берутся из style.css, поэтому тест ловит случайную правку,
из-за которой текст снова станет бледным.
"""

import re
from pathlib import Path

from django.test import SimpleTestCase

CSS = (
    Path(__file__).resolve().parents[2]
    / "documents" / "static" / "documents" / "css" / "style.css"
)

AA_TEXT = 4.5
AA_UI = 3.0


def _channel(value: int) -> float:
    c = value / 255
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def luminance(hex_color: str) -> float:
    value = hex_color.lstrip("#")
    red, green, blue = (int(value[index:index + 2], 16) for index in (0, 2, 4))
    return 0.2126 * _channel(red) + 0.7152 * _channel(green) + 0.0722 * _channel(blue)


def contrast(first: str, second: str) -> float:
    a, b = luminance(first), luminance(second)
    high, low = max(a, b), min(a, b)
    return (high + 0.05) / (low + 0.05)


class PaletteContrastTestCase(SimpleTestCase):
    def setUp(self):
        text = CSS.read_text(encoding="utf-8")
        self.tokens = dict(re.findall(r"(--[a-z0-9-]+):\s*(#[0-9a-fA-F]{6});", text))
        self.paper = self.tokens["--paper"]

    def test_tokens_are_parsed(self):
        for name in ("--paper", "--ink", "--muted", "--faint", "--accent"):
            self.assertIn(name, self.tokens)

    def test_main_text_passes_aa(self):
        self.assertGreaterEqual(contrast(self.tokens["--ink"], self.paper), AA_TEXT)

    def test_muted_text_passes_aa(self):
        self.assertGreaterEqual(contrast(self.tokens["--muted"], self.paper), AA_TEXT)

    def test_faint_is_usable_for_icons(self):
        # Для нетекстовых элементов норма 3:1
        self.assertGreaterEqual(contrast(self.tokens["--faint"], self.paper), AA_UI)

    def test_accent_links_pass_aa(self):
        self.assertGreaterEqual(contrast(self.tokens["--accent"], self.paper), AA_TEXT)
