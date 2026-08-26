"""Сборка кадра истории VK 9:16: стили bubble (текущий) и social (компактный IG-like)."""
from __future__ import annotations

import io
import logging
import os
from typing import List, Optional, Sequence, Tuple

from PIL import Image, ImageDraw, ImageFilter, ImageFont

logger = logging.getLogger(__name__)

STORY_WIDTH = 1080
STORY_HEIGHT = 1920
MAX_PHOTOS = 6

STORY_STYLE_BUBBLE = "bubble"
STORY_STYLE_SOCIAL = "social"
STORY_STYLES = (STORY_STYLE_BUBBLE, STORY_STYLE_SOCIAL)

CARD_BG = (255, 255, 255, 255)
TEXT_FG = (20, 20, 20, 255)
PRICE_FG = (0, 0, 0, 255)

_FONT_CACHE: dict[tuple[int, bool], ImageFont.ImageFont] = {}
_FONT_SOURCE: Optional[str] = None


def normalize_story_style(style: Optional[str]) -> str:
    value = (style or STORY_STYLE_BUBBLE).strip().lower()
    return value if value in STORY_STYLES else STORY_STYLE_BUBBLE


def story_style_label(style: Optional[str]) -> str:
    """Короткая подпись стиля для UI (рус.)."""
    if normalize_story_style(style) == STORY_STYLE_SOCIAL:
        return "Компакт"
    return "Карточка"


STORIES_MODE_OFF = "off"


def stories_mode_button_label(*, enabled: bool, style: Optional[str] = None) -> str:
    """Текст кнопки 3-state: выкл / компакт / карточка."""
    if not enabled:
        return "🔴 Сторис: выкл"
    if normalize_story_style(style) == STORY_STYLE_SOCIAL:
        return "🟢 Сторис: компакт"
    return "🟢 Сторис: карточка"


def stories_mode_toast(mode: str) -> str:
    """Короткий ответ callback после цикла режима автосторис."""
    if mode == STORIES_MODE_OFF or mode == "off":
        return "Сторис: выкл"
    if normalize_story_style(mode) == STORY_STYLE_SOCIAL:
        return "Сторис: компакт"
    return "Сторис: карточка"


def stories_mode_status_line(*, enabled: bool, style: Optional[str] = None) -> str:
    """Строка статуса для экрана настроек / списка интервалов."""
    if not enabled:
        return "🔴 Сторис: выкл"
    return f"🟢 Сторис: {story_style_label(style).lower()}"


def _font_candidates(bold: bool) -> Tuple[str, ...]:
    base = os.path.join(os.path.dirname(__file__), "..", "..", "assets", "fonts")
    if bold:
        return (
            os.path.join(base, "Inter-Bold.ttf"),
            os.path.join(base, "DejaVuSans-Bold.ttf"),
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        )
    return (
        os.path.join(base, "Inter-Regular.ttf"),
        os.path.join(base, "DejaVuSans.ttf"),
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        os.path.join(base, "DejaVuSans-Bold.ttf"),
    )


def _load_font(size: int, *, bold: bool = False) -> ImageFont.ImageFont:
    global _FONT_SOURCE
    key = (size, bold)
    cached = _FONT_CACHE.get(key)
    if cached is not None:
        return cached
    for path in _font_candidates(bold):
        try:
            font = ImageFont.truetype(path, size)
            _FONT_CACHE[key] = font
            _FONT_SOURCE = path
            return font
        except OSError:
            continue
    try:
        font = ImageFont.load_default(size=size)
        _FONT_SOURCE = f"pillow_default_size_{size}"
    except TypeError:
        font = ImageFont.load_default()
        _FONT_SOURCE = "pillow_default_nosize"
    _FONT_CACHE[key] = font
    return font


def font_source() -> Optional[str]:
    return _FONT_SOURCE


def normalize_price(price: Optional[str]) -> str:
    price = (price or "").strip()
    if not price:
        return ""
    price_norm = (
        price.replace("₽", "р.")
        .replace("руб.", "р.")
        .replace("руб", "р.")
        .replace("RUB", "р.")
    )
    if not price_norm.endswith(("р.", "р", "₽")):
        price_norm = f"{price_norm}р."
    return price_norm


def _open_rgb(data: bytes) -> Optional[Image.Image]:
    try:
        img = Image.open(io.BytesIO(data))
        img = img.convert("RGB")
        img.load()
        return img
    except Exception as e:
        logger.warning("Failed to open story photo: %s", e)
        return None


def _cover_crop(img: Image.Image, width: int, height: int) -> Image.Image:
    src_w, src_h = img.size
    if src_w <= 0 or src_h <= 0:
        return Image.new("RGB", (width, height), (60, 60, 60))
    scale = max(width / src_w, height / src_h)
    new_w = max(1, int(round(src_w * scale)))
    new_h = max(1, int(round(src_h * scale)))
    resized = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
    left = max(0, (new_w - width) // 2)
    top = max(0, (new_h - height) // 2)
    return resized.crop((left, top, left + width, top + height))


def _rounded_mask(size: Tuple[int, int], radius: int) -> Image.Image:
    mask = Image.new("L", size, 0)
    draw = ImageDraw.Draw(mask)
    draw.rounded_rectangle((0, 0, size[0], size[1]), radius=radius, fill=255)
    return mask


def _paste_rounded(canvas: Image.Image, img: Image.Image, xy: Tuple[int, int], radius: int) -> None:
    if img.mode != "RGBA":
        img = img.convert("RGBA")
    mask = _rounded_mask(img.size, radius)
    canvas.paste(img, xy, mask)


def _circular_avatar(img: Image.Image, size: int) -> Image.Image:
    cover = _cover_crop(img.convert("RGB"), size, size).convert("RGBA")
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size - 1, size - 1), fill=255)
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(cover, (0, 0), mask)
    return out


def _layout_cells(count: int, area: Tuple[int, int, int, int], *, gap: int = 6) -> List[Tuple[int, int, int, int]]:
    left, top, right, bottom = area
    width = right - left
    height = bottom - top
    count = max(1, min(count, MAX_PHOTOS))

    def cell(col: int, row: int, cols: int, rows: int):
        cell_w = (width - gap * (cols - 1)) // cols
        cell_h = (height - gap * (rows - 1)) // rows
        x = left + col * (cell_w + gap)
        y = top + row * (cell_h + gap)
        return (x, y, cell_w, cell_h)

    if count == 1:
        return [cell(0, 0, 1, 1)]
    if count == 2:
        return [cell(0, 0, 2, 1), cell(1, 0, 2, 1)]
    if count == 3:
        top_h = (height - gap) // 2
        bot_h = height - gap - top_h
        bot_w = (width - gap) // 2
        return [
            (left, top, width, top_h),
            (left, top + top_h + gap, bot_w, bot_h),
            (left + bot_w + gap, top + top_h + gap, width - bot_w - gap, bot_h),
        ]
    if count == 4:
        return [cell(c, r, 2, 2) for r in range(2) for c in range(2)]
    if count == 5:
        top_h = (height - gap) * 3 // 5
        bot_h = height - gap - top_h
        top_w = (width - gap) // 2
        cells = [
            (left, top, top_w, top_h),
            (left + top_w + gap, top, width - top_w - gap, top_h),
        ]
        bot_y = top + top_h + gap
        bot_w = (width - 2 * gap) // 3
        for i in range(3):
            w = bot_w if i < 2 else width - 2 * (bot_w + gap)
            cells.append((left + i * (bot_w + gap), bot_y, w, bot_h))
        return cells

    top_h = (height - gap) * 3 // 5
    bot_h = height - gap - top_h
    top_w = (width - gap) // 2
    cells = [
        (left, top, top_w, top_h),
        (left + top_w + gap, top, width - top_w - gap, top_h),
    ]
    bot_y = top + top_h + gap
    bot_w = (width - 3 * gap) // 4
    for i in range(4):
        w = bot_w if i < 3 else width - 3 * (bot_w + gap)
        cells.append((left + i * (bot_w + gap), bot_y, w, bot_h))
    return cells


def _wrap_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.ImageFont,
    max_width: int,
    *,
    max_lines: Optional[int] = None,
) -> List[str]:
    words = text.split()
    if not words:
        return []
    lines: List[str] = []
    current = words[0]
    for word in words[1:]:
        trial = f"{current} {word}"
        bbox = draw.textbbox((0, 0), trial, font=font)
        if bbox[2] - bbox[0] <= max_width:
            current = trial
        else:
            lines.append(current)
            current = word
    lines.append(current)
    if max_lines is not None and len(lines) > max_lines:
        lines = lines[:max_lines]
        last = lines[-1]
        while True:
            bbox = draw.textbbox((0, 0), last + "…", font=font)
            if bbox[2] - bbox[0] <= max_width or len(last) <= 1:
                lines[-1] = last + "…"
                break
            last = last[:-1]
    return lines


def _make_blur_background(photo: Image.Image, *, darken: float = 0.22) -> Image.Image:
    cover = _cover_crop(photo, STORY_WIDTH, STORY_HEIGHT)
    blurred = cover.filter(ImageFilter.GaussianBlur(radius=28))
    overlay = Image.new("RGB", (STORY_WIDTH, STORY_HEIGHT), (0, 0, 0))
    return Image.blend(blurred, overlay, darken)


def _load_photos(photo_blobs: Sequence[bytes]) -> List[Image.Image]:
    images: List[Image.Image] = []
    for blob in photo_blobs:
        img = _open_rgb(blob)
        if img is not None:
            images.append(img)
        if len(images) >= MAX_PHOTOS:
            break
    return images


def _text_block_height(
    probe: ImageDraw.ImageDraw,
    lines: List[str],
    font: ImageFont.ImageFont,
    *,
    line_gap: int,
) -> int:
    if not lines:
        return 0
    total = 0
    for ln in lines:
        bb = probe.textbbox((0, 0), ln, font=font)
        total += (bb[3] - bb[1]) + line_gap
    return max(0, total - line_gap)


def _draw_header(
    card: Image.Image,
    *,
    pad: int,
    header_h: int,
    brand: str,
    brand_font: ImageFont.ImageFont,
    logo_blob: Optional[bytes],
    avatar_size: int,
) -> None:
    ax, ay = pad, (header_h - avatar_size) // 2
    if logo_blob:
        logo_img = _open_rgb(logo_blob)
        if logo_img is not None:
            avatar = _circular_avatar(logo_img, avatar_size)
            card.paste(avatar, (ax, ay), avatar)
        else:
            d = ImageDraw.Draw(card)
            d.ellipse((ax, ay, ax + avatar_size, ay + avatar_size), fill=(230, 230, 230, 255))
    else:
        d = ImageDraw.Draw(card)
        d.ellipse((ax, ay, ax + avatar_size, ay + avatar_size), fill=(230, 230, 230, 255))

    d = ImageDraw.Draw(card)
    bb = d.textbbox((0, 0), brand, font=brand_font)
    bh = bb[3] - bb[1]
    d.text((ax + avatar_size + 14, (header_h - bh) // 2 - 2), brand, font=brand_font, fill=TEXT_FG)


def _composite_card(
    bg: Image.Image,
    card: Image.Image,
    *,
    card_x: int,
    card_top: int,
    radius: int,
) -> Image.Image:
    card_w, card_h = card.size
    shadow = Image.new("RGBA", (card_w + 40, card_h + 40), (0, 0, 0, 0))
    sdraw = ImageDraw.Draw(shadow)
    sdraw.rounded_rectangle((20, 24, card_w + 20, card_h + 24), radius=radius + 4, fill=(0, 0, 0, 70))
    shadow = shadow.filter(ImageFilter.GaussianBlur(radius=16))
    bg.alpha_composite(shadow, (card_x - 20, card_top - 20))
    mask = _rounded_mask((card_w, card_h), radius)
    bg.paste(card, (card_x, card_top), mask)
    out = Image.new("RGB", (STORY_WIDTH, STORY_HEIGHT), (30, 30, 30))
    out.paste(bg, mask=bg.split()[-1])
    return out


def _build_bubble_frame(
    images: List[Image.Image],
    *,
    title_text: str,
    price_text: str,
    brand: str,
    logo_blob: Optional[bytes],
) -> bytes:
    bg = _make_blur_background(images[0]).convert("RGBA")

    card_w = 920
    card_x = (STORY_WIDTH - card_w) // 2
    pad = 28
    header_h = 88
    caption_gap = 18
    title_font = _load_font(40, bold=True)
    price_font = _load_font(56, bold=True)
    brand_font = _load_font(34, bold=True)

    probe = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    caption_max_w = card_w - pad * 2
    title_lines = _wrap_text(probe, title_text, title_font, caption_max_w) if title_text else []
    price_lines = _wrap_text(probe, price_text, price_font, caption_max_w) if price_text else []
    title_h = _text_block_height(probe, title_lines, title_font, line_gap=6)
    price_h = _text_block_height(probe, price_lines, price_font, line_gap=4)
    caption_block_h = 0
    if title_lines or price_lines:
        caption_block_h = caption_gap + title_h + (10 if title_lines and price_lines else 0) + price_h + pad

    collage_h = max(720, 1500 - header_h - caption_block_h - pad)
    content_h = header_h + collage_h + caption_block_h
    card_h = content_h
    card_top = max(180, (STORY_HEIGHT - card_h) // 2 - 20)

    card = Image.new("RGBA", (card_w, card_h), CARD_BG)
    _draw_header(
        card,
        pad=pad,
        header_h=header_h,
        brand=brand,
        brand_font=brand_font,
        logo_blob=logo_blob,
        avatar_size=56,
    )

    collage_top = header_h
    collage_area = (pad, collage_top, card_w - pad, collage_top + collage_h)
    cells = _layout_cells(len(images), collage_area)
    for img, (x, y, w, h) in zip(images, cells):
        tile = _cover_crop(img, w, h).convert("RGBA")
        _paste_rounded(card, tile, (x, y), radius=10)

    d = ImageDraw.Draw(card)
    cy = collage_top + collage_h + caption_gap
    if title_lines:
        for ln in title_lines:
            bb = d.textbbox((0, 0), ln, font=title_font)
            d.text((pad, cy), ln, font=title_font, fill=TEXT_FG)
            cy += (bb[3] - bb[1]) + 6
        cy += 6
    if price_lines:
        for ln in price_lines:
            bb = d.textbbox((0, 0), ln, font=price_font)
            d.text((pad, cy), ln, font=price_font, fill=PRICE_FG)
            cy += (bb[3] - bb[1]) + 4

    out = _composite_card(bg, card, card_x=card_x, card_top=card_top, radius=36)
    buf = io.BytesIO()
    out.save(buf, format="JPEG", quality=92, optimize=True)
    return buf.getvalue()


def _build_social_frame(
    images: List[Image.Image],
    *,
    title_text: str,
    price_text: str,
    brand: str,
    logo_blob: Optional[bytes],
) -> bytes:
    """Компактный бабл: больше воздуха, почти без боковой белой рамки у коллажа."""
    bg = _make_blur_background(images[0], darken=0.28).convert("RGBA")

    card_w = 740
    card_x = (STORY_WIDTH - card_w) // 2
    side_pad = 0  # коллаж edge-to-edge по ширине карточки
    caption_pad = 22
    header_h = 72
    caption_gap = 14
    title_font = _load_font(34, bold=True)
    price_font = _load_font(52, bold=True)
    brand_font = _load_font(28, bold=True)

    probe = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    caption_max_w = card_w - caption_pad * 2
    title_lines = (
        _wrap_text(probe, title_text, title_font, caption_max_w, max_lines=2) if title_text else []
    )
    price_lines = _wrap_text(probe, price_text, price_font, caption_max_w, max_lines=1) if price_text else []
    title_h = _text_block_height(probe, title_lines, title_font, line_gap=5)
    price_h = _text_block_height(probe, price_lines, price_font, line_gap=2)
    caption_block_h = 0
    if title_lines or price_lines:
        caption_block_h = caption_gap + title_h + (8 if title_lines and price_lines else 0) + price_h + caption_pad

    # Ниже и уже — больше blur вокруг
    collage_h = 980
    content_h = header_h + collage_h + caption_block_h
    card_h = content_h
    card_top = max(260, (STORY_HEIGHT - card_h) // 2)

    card = Image.new("RGBA", (card_w, card_h), CARD_BG)
    _draw_header(
        card,
        pad=caption_pad,
        header_h=header_h,
        brand=brand,
        brand_font=brand_font,
        logo_blob=logo_blob,
        avatar_size=44,
    )

    collage_top = header_h
    collage_area = (side_pad, collage_top, card_w - side_pad, collage_top + collage_h)
    cells = _layout_cells(len(images), collage_area, gap=4)
    for img, (x, y, w, h) in zip(images, cells):
        tile = _cover_crop(img, w, h)
        # Без скругления ячеек у краёв — «как в IG»; внутренние стыки ровные
        card.paste(tile, (x, y))

    d = ImageDraw.Draw(card)
    cy = collage_top + collage_h + caption_gap
    if title_lines:
        for ln in title_lines:
            bb = d.textbbox((0, 0), ln, font=title_font)
            d.text((caption_pad, cy), ln, font=title_font, fill=TEXT_FG)
            cy += (bb[3] - bb[1]) + 5
        cy += 6
    if price_lines:
        for ln in price_lines:
            bb = d.textbbox((0, 0), ln, font=price_font)
            d.text((caption_pad, cy), ln, font=price_font, fill=PRICE_FG)
            cy += (bb[3] - bb[1]) + 2

    out = _composite_card(bg, card, card_x=card_x, card_top=card_top, radius=28)
    buf = io.BytesIO()
    out.save(buf, format="JPEG", quality=92, optimize=True)
    return buf.getvalue()


def build_story_image(
    photo_blobs: Sequence[bytes],
    *,
    title: Optional[str] = None,
    price: Optional[str] = None,
    footer_text: Optional[str] = None,  # ignored
    brand_name: Optional[str] = None,
    logo_blob: Optional[bytes] = None,
    style: Optional[str] = None,
) -> Optional[bytes]:
    """
    JPEG 1080x1920.
    style=bubble — крупный белый бабл (как сейчас).
    style=social — компактнее, больше воздуха, коллаж без боковой белой рамки.
    """
    _ = footer_text
    style_norm = normalize_story_style(style)
    images = _load_photos(photo_blobs)
    if not images:
        logger.error("No valid photos for story collage")
        return None

    title_text = (title or "").strip()
    price_text = normalize_price(price)
    brand = (brand_name or "appleshop43").strip()
    if brand.startswith("@"):
        brand = brand[1:]

    if style_norm == STORY_STYLE_SOCIAL:
        data = _build_social_frame(
            images,
            title_text=title_text,
            price_text=price_text,
            brand=brand,
            logo_blob=logo_blob,
        )
    else:
        data = _build_bubble_frame(
            images,
            title_text=title_text,
            price_text=price_text,
            brand=brand,
            logo_blob=logo_blob,
        )

    logger.info(
        "Story frame built (%s): title=%r price=%r brand=%r font=%s",
        style_norm,
        title_text,
        price_text,
        brand,
        _FONT_SOURCE,
    )
    return data


def banner_text_for_post(model_name: Optional[str], price: Optional[str]) -> str:
    title = (model_name or "").strip()
    price_norm = normalize_price(price)
    if title and price_norm:
        return f"{title} - {price_norm}"
    return title or price_norm or ""
