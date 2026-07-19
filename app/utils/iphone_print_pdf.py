"""Генерация одностраничного A4 PDF прайса iPhone."""
from __future__ import annotations

import io
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

from app.utils.iphone_print_price import PrintPriceLine, build_print_catalog
from app.utils.iphone_print_price_config import (
    BODY_FONT_SIZE,
    BODY_LEADING,
    COLUMN_GAP_MM,
    MIN_BODY_FONT_SIZE,
    PAGE_MARGIN_MM,
    SECTION_FONT_SIZE,
    SUBTITLE,
    SUBTITLE_FONT_SIZE,
    TITLE,
    TITLE_FONT_SIZE,
    TRADEIN_SECTION_TITLE,
)

_FONTS_DIR = Path(__file__).resolve().parent.parent / "assets" / "fonts"
_FONT_REG = "DejaVuSans"
_FONT_BOLD = "DejaVuSans-Bold"
_fonts_registered = False


def _ensure_fonts() -> None:
    global _fonts_registered
    if _fonts_registered:
        return
    regular = _FONTS_DIR / "DejaVuSans.ttf"
    bold = _FONTS_DIR / "DejaVuSans-Bold.ttf"
    if not regular.is_file() or not bold.is_file():
        raise FileNotFoundError(
            f"Шрифты не найдены в {_FONTS_DIR}. Нужны DejaVuSans.ttf и DejaVuSans-Bold.ttf"
        )
    pdfmetrics.registerFont(TTFont(_FONT_REG, str(regular)))
    pdfmetrics.registerFont(TTFont(_FONT_BOLD, str(bold)))
    _fonts_registered = True


def _draw_wrapped_title(c: canvas.Canvas, text: str, x: float, y: float, font: str, size: float) -> float:
    c.setFont(font, size)
    c.drawString(x, y, text)
    # Подчёркивание
    w = c.stringWidth(text, font, size)
    c.setStrokeColorRGB(0, 0, 0)
    c.setLineWidth(0.8)
    c.line(x, y - 1.5, x + w, y - 1.5)
    return y - size - 4


def _column_height_needed(
    lines: Sequence[PrintPriceLine],
    *,
    body_size: float,
    leading: float,
    extra_section: bool = False,
    tradein: Sequence[PrintPriceLine] = (),
) -> float:
    h = 0.0
    for L in lines:
        h += leading if not L.is_blank else leading * 0.55
    if extra_section:
        h += SECTION_FONT_SIZE + 8
        h += leading * 0.4
        for L in tradein:
            h += leading
    return h


def _draw_column(
    c: canvas.Canvas,
    lines: Sequence[PrintPriceLine],
    x: float,
    y_top: float,
    width: float,
    *,
    body_size: float,
    leading: float,
) -> float:
    y = y_top
    c.setFillColorRGB(0, 0, 0)
    for L in lines:
        if L.is_blank:
            y -= leading * 0.55
            continue
        c.setFont(_FONT_REG, body_size)
        # Обрезка слишком длинных строк
        text = L.text
        max_w = width - 2
        while text and c.stringWidth(text, _FONT_REG, body_size) > max_w:
            text = text[:-1]
        c.drawString(x, y - body_size, text)
        y -= leading
    return y


def build_iphone_price_pdf_bytes(
    new_products: Optional[List[dict]] = None,
    tradein_products: Optional[List[dict]] = None,
) -> bytes:
    """Собирает PDF в память (одна страница A4)."""
    _ensure_fonts()
    left, right, tradein = build_print_catalog(new_products, tradein_products)

    page_w, page_h = A4
    margin = PAGE_MARGIN_MM * mm
    gap = COLUMN_GAP_MM * mm
    usable_w = page_w - 2 * margin
    col_w = (usable_w - gap) / 2.0

    body_size = BODY_FONT_SIZE
    leading = BODY_LEADING

    # Автоподгонка кегля под одну страницу
    for _ in range(12):
        # Заголовок занимает ~ title + subtitle + отступы
        header_h = TITLE_FONT_SIZE + 8 + SUBTITLE_FONT_SIZE + 14
        y_content_top = page_h - margin - header_h
        avail = y_content_top - margin
        left_h = _column_height_needed(left, body_size=body_size, leading=leading)
        right_h = _column_height_needed(
            right,
            body_size=body_size,
            leading=leading,
            extra_section=True,
            tradein=tradein,
        )
        if max(left_h, right_h) <= avail or body_size <= MIN_BODY_FONT_SIZE:
            break
        body_size -= 0.35
        leading = body_size + 2.0

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)

    y = page_h - margin - TITLE_FONT_SIZE
    y = _draw_wrapped_title(c, TITLE, margin, y, _FONT_BOLD, TITLE_FONT_SIZE)
    y -= 4
    c.setFont(_FONT_BOLD, SUBTITLE_FONT_SIZE)
    c.drawString(margin, y, SUBTITLE)
    y -= SUBTITLE_FONT_SIZE + 12

    left_x = margin
    right_x = margin + col_w + gap

    _draw_column(c, left, left_x, y, col_w, body_size=body_size, leading=leading)
    y_right = _draw_column(c, right, right_x, y, col_w, body_size=body_size, leading=leading)

    # Секция обменок — всегда в правой колонке
    y_sec = y_right - 8
    c.setFont(_FONT_BOLD, SECTION_FONT_SIZE)
    sec = TRADEIN_SECTION_TITLE
    c.drawString(right_x, y_sec - SECTION_FONT_SIZE, sec)
    sw = c.stringWidth(sec, _FONT_BOLD, SECTION_FONT_SIZE)
    c.setLineWidth(0.7)
    c.line(right_x, y_sec - SECTION_FONT_SIZE - 1.5, right_x + sw, y_sec - SECTION_FONT_SIZE - 1.5)
    y_sec -= SECTION_FONT_SIZE + 6
    _draw_column(
        c,
        tradein,
        right_x,
        y_sec,
        col_w,
        body_size=body_size,
        leading=leading,
    )

    c.showPage()
    c.save()
    return buf.getvalue()


def pdf_page_count(pdf_bytes: bytes) -> int:
    """Грубая проверка числа страниц по маркерам PDF."""
    return pdf_bytes.count(b"/Type /Page") - pdf_bytes.count(b"/Type /Pages")
