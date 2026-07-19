"""Генерация одностраничного A4 PDF прайса iPhone."""
from __future__ import annotations

import io
from pathlib import Path
from typing import List, Optional, Sequence

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

from app.utils.iphone_print_price import PrintPriceLine, build_print_catalog
from app.utils.iphone_print_price_config import (
    BLANK_LINE_FACTOR,
    BODY_FONT_SIZE,
    COLUMN_GAP_MM,
    MAX_BODY_FONT_SIZE,
    MIN_BODY_FONT_SIZE,
    PAGE_MARGIN_MM,
    SECTION_FONT_SIZE,
    STOCK_LEGEND,
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
_LEGEND_FONT_SIZE = 7.5


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
    w = c.stringWidth(text, font, size)
    c.setStrokeColorRGB(0, 0, 0)
    c.setLineWidth(0.9)
    c.line(x, y - 1.5, x + w, y - 1.5)
    return y - size - 3


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
        h += leading if not L.is_blank else leading * BLANK_LINE_FACTOR
    if extra_section:
        h += SECTION_FONT_SIZE + 6
        h += leading * 0.3
        for L in tradein:
            h += leading
    return h


def _longest_line_width(
    c: canvas.Canvas,
    lines: Sequence[PrintPriceLine],
    *,
    font: str,
    size: float,
) -> float:
    widest = 0.0
    for L in lines:
        if L.is_blank or not L.text:
            continue
        widest = max(widest, c.stringWidth(L.text, font, size))
    return widest


def _fit_body_size(
    c: canvas.Canvas,
    left: Sequence[PrintPriceLine],
    right: Sequence[PrintPriceLine],
    tradein: Sequence[PrintPriceLine],
    avail: float,
    col_w: float,
) -> tuple[float, float]:
    """Максимальный кегль: влезает по высоте и все строки — по ширине колонки."""
    body_size = min(MAX_BODY_FONT_SIZE, max(BODY_FONT_SIZE, MIN_BODY_FONT_SIZE))
    leading = body_size + 1.7
    max_text_w = col_w - 2

    def fits(size: float, lead: float) -> bool:
        left_h = _column_height_needed(left, body_size=size, leading=lead)
        right_h = _column_height_needed(
            right,
            body_size=size,
            leading=lead,
            extra_section=True,
            tradein=tradein,
        )
        if max(left_h, right_h) > avail:
            return False
        all_lines = list(left) + list(right) + list(tradein)
        return _longest_line_width(c, all_lines, font=_FONT_BOLD, size=size) <= max_text_w

    while body_size > MIN_BODY_FONT_SIZE and not fits(body_size, leading):
        body_size -= 0.25
        leading = body_size + 1.7

    while body_size < MAX_BODY_FONT_SIZE:
        nxt = body_size + 0.25
        nxt_lead = nxt + 1.7
        if not fits(nxt, nxt_lead):
            break
        body_size = nxt
        leading = nxt_lead

    return body_size, leading


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
            y -= leading * BLANK_LINE_FACTOR
            continue
        c.setFont(_FONT_BOLD, body_size)
        text = L.text
        max_w = width - 2
        while text and c.stringWidth(text, _FONT_BOLD, body_size) > max_w:
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

    header_h = TITLE_FONT_SIZE + 6 + SUBTITLE_FONT_SIZE + 10
    legend_h = _LEGEND_FONT_SIZE + 8
    y_content_top = page_h - margin - header_h
    avail = y_content_top - margin - legend_h

    measure_buf = io.BytesIO()
    measure_c = canvas.Canvas(measure_buf, pagesize=A4)
    body_size, leading = _fit_body_size(
        measure_c, left, right, tradein, avail, col_w
    )

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)

    y = page_h - margin - TITLE_FONT_SIZE
    y = _draw_wrapped_title(c, TITLE, margin, y, _FONT_BOLD, TITLE_FONT_SIZE)
    y -= 3
    c.setFont(_FONT_BOLD, SUBTITLE_FONT_SIZE)
    c.drawString(margin, y, SUBTITLE)
    y -= SUBTITLE_FONT_SIZE + 8

    left_x = margin
    right_x = margin + col_w + gap

    _draw_column(c, left, left_x, y, col_w, body_size=body_size, leading=leading)
    y_right = _draw_column(c, right, right_x, y, col_w, body_size=body_size, leading=leading)

    y_sec = y_right - 6
    c.setFont(_FONT_BOLD, SECTION_FONT_SIZE)
    sec = TRADEIN_SECTION_TITLE
    c.drawString(right_x, y_sec - SECTION_FONT_SIZE, sec)
    sw = c.stringWidth(sec, _FONT_BOLD, SECTION_FONT_SIZE)
    c.setLineWidth(0.8)
    c.line(right_x, y_sec - SECTION_FONT_SIZE - 1.5, right_x + sw, y_sec - SECTION_FONT_SIZE - 1.5)
    y_sec -= SECTION_FONT_SIZE + 5
    _draw_column(
        c,
        tradein,
        right_x,
        y_sec,
        col_w,
        body_size=body_size,
        leading=leading,
    )

    # Легенда маркера наличия — мелко внизу страницы
    c.setFont(_FONT_REG, _LEGEND_FONT_SIZE)
    c.setFillColorRGB(0.25, 0.25, 0.25)
    c.drawString(margin, margin, STOCK_LEGEND)

    c.showPage()
    c.save()
    return buf.getvalue()


def pdf_page_count(pdf_bytes: bytes) -> int:
    """Грубая проверка числа страниц по маркерам PDF."""
    return pdf_bytes.count(b"/Type /Page") - pdf_bytes.count(b"/Type /Pages")
