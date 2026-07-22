"""Генерация PDF-ценников A4 (сетка 4×4, автопагинация)."""
from __future__ import annotations

import io
from typing import List, Optional, Sequence, Tuple

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

from app.utils.price_tag_config import (
    CASH_LABEL_FONT,
    CASH_LABEL_LINES,
    CASH_PRICE_FONT,
    CELL_GAP_H_MM,
    CELL_GAP_V_MM,
    CELL_HEIGHT_MM,
    CELL_WIDTH_MM,
    COLS,
    CONTENT_HEIGHT_MM,
    CONTENT_INSET_MM,
    CONTENT_WIDTH_MM,
    DESC_FONT_MAX,
    DESC_FONT_MIN,
    FOOTER_FONT,
    PAGE_MARGIN_LEFT_MM,
    PAGE_MARGIN_TOP_MM,
    ROWS,
    SIGNATURE_TEXT,
    STRIKE_LABEL_FONT,
    STRIKE_LABEL_LINES,
    STRIKE_PRICE_FONT,
    SUBTITLE_FONT,
    TAGS_PER_PAGE,
    TITLE_FONT_MAX,
    TITLE_FONT_MIN,
    ZONE_DESC_MM,
    ZONE_FOOTER_MM,
    ZONE_HEADER_MM,
    ZONE_PRICE_MM,
)
from app.utils.price_tag_data import PriceTagItem, build_price_tag_items

_FONTS_DIR = __import__("pathlib").Path(__file__).resolve().parent.parent / "assets" / "fonts"
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


def _wrap_lines(text: str, font: str, size: float, max_width: float) -> List[str]:
    words = (text or "").split()
    if not words:
        return []
    lines: List[str] = []
    current = words[0]
    for word in words[1:]:
        trial = f"{current} {word}"
        if pdfmetrics.stringWidth(trial, font, size) <= max_width:
            current = trial
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def _fit_wrapped_lines(
    text: str,
    font: str,
    max_size: float,
    min_size: float,
    max_width: float,
    max_lines: int,
) -> tuple[List[str], float]:
    size = max_size
    while size >= min_size:
        lines = _wrap_lines(text, font, size, max_width)
        if len(lines) <= max_lines:
            return lines, size
        size -= 0.25
    lines = _wrap_lines(text, font, min_size, max_width)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        if lines:
            last = lines[-1]
            while last and pdfmetrics.stringWidth(last + "…", font, min_size) > max_width:
                last = last[:-1]
            lines[-1] = (last + "…") if last else "…"
    return lines, min_size


def _line_visual_height(font: str, size: float) -> float:
    ascent = pdfmetrics.getAscent(font) / 1000.0 * size
    descent = abs(pdfmetrics.getDescent(font) / 1000.0 * size)
    return ascent + descent


def _block_visual_height(specs: Sequence[Tuple[str, str, float]], *, gap: float = 1.2) -> float:
    if not specs:
        return 0.0
    total = sum(_line_visual_height(f, s) for f, _, s in specs)
    total += gap * max(0, len(specs) - 1)
    return total


def _fit_wrapped_lines_in_zone(
    text: str,
    font: str,
    max_size: float,
    min_size: float,
    max_width: float,
    max_lines: int,
    zone_height_pt: float,
) -> tuple[List[str], float]:
    size = max_size
    while size >= min_size:
        lines = _wrap_lines(text, font, size, max_width)
        if len(lines) > max_lines:
            size -= 0.25
            continue
        specs = [(font, ln, size) for ln in lines]
        if _block_visual_height(specs) <= zone_height_pt - 1.0:
            return lines, size
        size -= 0.25
    lines = _wrap_lines(text, font, min_size, max_width)[:max_lines]
    if lines:
        last = lines[-1]
        while last and pdfmetrics.stringWidth(last + "…", font, min_size) > max_width:
            last = last[:-1]
        lines[-1] = (last + "…") if last else "…"
    return lines, min_size


def _draw_centered_block(
    c: canvas.Canvas,
    lines: Sequence[Tuple[str, str, float]],
    x: float,
    y_bottom: float,
    w: float,
    h: float,
) -> None:
    """Рисует блок строк по центру зоны (горизонтально и вертикально)."""
    specs = [(font, text, size) for font, text, size in lines if text]
    if not specs:
        return
    gap = 1.2
    block_h = _block_visual_height(specs, gap=gap)
    # Старт от верха блока — строки рисуем сверху вниз (порядок чтения)
    y_top = y_bottom + (h + block_h) / 2
    y_cursor = y_top
    c.setFillColorRGB(0, 0, 0)
    for i, (font, text, size) in enumerate(specs):
        ascent = pdfmetrics.getAscent(font) / 1000.0 * size
        baseline = y_cursor - ascent
        c.setFont(font, size)
        tw = c.stringWidth(text, font, size)
        c.drawString(x + max(0, (w - tw) / 2), baseline, text)
        lh = _line_visual_height(font, size)
        y_cursor -= lh + (gap if i < len(specs) - 1 else 0)


def _draw_zone_dividers(
    c: canvas.Canvas,
    cx: float,
    cy_bottom: float,
    cw: float,
) -> None:
    """Горизонтальные линии между зонами (как в макете 1С)."""
    c.setStrokeColorRGB(0, 0, 0)
    c.setLineWidth(0.5)
    y1 = cy_bottom + ZONE_FOOTER_MM * mm
    y2 = y1 + ZONE_PRICE_MM * mm
    y3 = y2 + ZONE_DESC_MM * mm
    for y_line in (y1, y2, y3):
        c.line(cx, y_line, cx + cw, y_line)


def _draw_price_row_in_zone(
    c: canvas.Canvas,
    label_lines: Sequence[str],
    amount: str,
    x: float,
    y_bottom: float,
    w: float,
    h: float,
    *,
    label_font: str,
    label_size: float,
    price_font: str,
    price_size: float,
) -> None:
    label_leading = label_size + 0.8
    label_block_h = label_size + (len(label_lines) - 1) * label_leading if label_lines else 0

    price_text = f"₽ {amount}"
    c.setFont(price_font, price_size)
    pw = c.stringWidth(price_text, price_font, price_size)

    label_x = x + 1.5
    price_x = x + w - pw - 1.5

    block_h = max(label_block_h, price_size)
    base_y = y_bottom + (h - block_h) / 2

    if label_lines:
        c.setFont(label_font, label_size)
        label_y = base_y + label_block_h - label_size
        for i, line in enumerate(label_lines):
            c.drawString(label_x, label_y - i * label_leading, line)

    c.setFont(price_font, price_size)
    price_y = base_y + (block_h - price_size) / 2
    c.drawString(price_x, price_y, price_text)


def _cell_origin(page_w: float, page_h: float, slot: int) -> tuple[float, float]:
    """Нижний левый угол ячейки (ReportLab coords)."""
    local = slot % TAGS_PER_PAGE
    col = local % COLS
    row = local // COLS
    x = PAGE_MARGIN_LEFT_MM * mm + col * (CELL_WIDTH_MM * mm + CELL_GAP_H_MM * mm)
    y = page_h - PAGE_MARGIN_TOP_MM * mm - (row + 1) * (CELL_HEIGHT_MM * mm + CELL_GAP_V_MM * mm)
    return x, y


def _draw_tag(c: canvas.Canvas, item: PriceTagItem, x: float, y: float) -> None:
    """Рисует один ценник: 4 зоны с разделителями."""
    cw = CELL_WIDTH_MM * mm
    ch = CELL_HEIGHT_MM * mm
    inset = CONTENT_INSET_MM * mm
    cx = x + inset
    cy_bottom = y + inset
    cw_content = CONTENT_WIDTH_MM * mm
    ch_content = CONTENT_HEIGHT_MM * mm

    # Внешняя рамка ячейки
    c.setStrokeColorRGB(0, 0, 0)
    c.setLineWidth(0.6)
    c.rect(x, y, cw, ch, stroke=1, fill=0)

    _draw_zone_dividers(c, cx, cy_bottom, cw_content)

    # --- Зона 1: заголовок (20 мм) — название + подзаголовок по центру ---
    header_bottom = cy_bottom + (ZONE_FOOTER_MM + ZONE_PRICE_MM + ZONE_DESC_MM) * mm
    header_h = ZONE_HEADER_MM * mm

    title_lines, title_size = _fit_wrapped_lines_in_zone(
        item.name,
        _FONT_BOLD,
        TITLE_FONT_MAX,
        TITLE_FONT_MIN,
        cw_content - 2,
        max_lines=2 if item.subtitle else 3,
        zone_height_pt=header_h - (SUBTITLE_FONT + 2 if item.subtitle else 0),
    )
    block_lines: List[Tuple[str, str, float]] = [
        (_FONT_BOLD, line, title_size) for line in title_lines
    ]
    if item.subtitle:
        block_lines.append((_FONT_BOLD, item.subtitle, SUBTITLE_FONT))
    _draw_centered_block(c, block_lines, cx, header_bottom, cw_content, header_h)

    # --- Зона 2: описание (16.5 мм) ---
    desc_bottom = cy_bottom + (ZONE_FOOTER_MM + ZONE_PRICE_MM) * mm
    desc_h = ZONE_DESC_MM * mm
    if item.description:
        desc_lines, desc_size = _fit_wrapped_lines_in_zone(
            item.description,
            _FONT_REG,
            DESC_FONT_MAX,
            DESC_FONT_MIN,
            cw_content - 2,
            max_lines=4,
            zone_height_pt=desc_h,
        )
        desc_block = [(_FONT_REG, line, desc_size) for line in desc_lines]
        _draw_centered_block(c, desc_block, cx, desc_bottom, cw_content, desc_h)

    # --- Зона 3: цены (17 мм) — две строки с переносом подписей ---
    price_bottom = cy_bottom + ZONE_FOOTER_MM * mm
    price_h = ZONE_PRICE_MM * mm
    row_h = price_h / 2.0

    _draw_price_row_in_zone(
        c,
        CASH_LABEL_LINES,
        item.cash_price_display,
        cx,
        price_bottom + row_h,
        cw_content,
        row_h,
        label_font=_FONT_BOLD,
        label_size=CASH_LABEL_FONT,
        price_font=_FONT_BOLD,
        price_size=CASH_PRICE_FONT,
    )
    _draw_price_row_in_zone(
        c,
        STRIKE_LABEL_LINES,
        item.strike_price_display,
        cx,
        price_bottom,
        cw_content,
        row_h,
        label_font=_FONT_REG,
        label_size=STRIKE_LABEL_FONT,
        price_font=_FONT_BOLD,
        price_size=STRIKE_PRICE_FONT,
    )

    # --- Зона 4: футер (4 мм) ---
    footer_h = ZONE_FOOTER_MM * mm
    c.setFont(_FONT_REG, FOOTER_FONT)
    c.setFillColorRGB(0, 0, 0)
    footer_y = cy_bottom + (footer_h - FOOTER_FONT) / 2
    c.drawString(cx + 1, footer_y, SIGNATURE_TEXT)
    date_w = c.stringWidth(item.print_date, _FONT_REG, FOOTER_FONT)
    c.drawString(cx + cw_content - date_w - 1, footer_y, item.print_date)


def build_price_tags_pdf_bytes(
    product_ids: Sequence[int],
    items: Optional[List[PriceTagItem]] = None,
) -> bytes:
    """PDF с ценниками; 16 на страницу, далее новая страница."""
    _ensure_fonts()
    tag_items = items if items is not None else build_price_tag_items(product_ids)
    if not tag_items:
        raise ValueError("Нет товаров для печати ценников")

    buf = io.BytesIO()
    page_w, page_h = A4
    c = canvas.Canvas(buf, pagesize=A4)

    for idx, item in enumerate(tag_items):
        if idx > 0 and idx % TAGS_PER_PAGE == 0:
            c.showPage()
        x, y = _cell_origin(page_w, page_h, idx % TAGS_PER_PAGE)
        _draw_tag(c, item, x, y)

    c.showPage()
    c.save()
    return buf.getvalue()


def pdf_page_count(pdf_bytes: bytes) -> int:
    """Грубая проверка числа страниц по маркерам PDF."""
    return max(1, pdf_bytes.count(b"/Type /Page") - pdf_bytes.count(b"/Type /Pages"))
