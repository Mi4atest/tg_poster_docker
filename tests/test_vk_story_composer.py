"""Тесты сборки кадра истории VK."""
from io import BytesIO

from PIL import Image, ImageDraw

from app.workers.vk.story_composer import (
    STORY_HEIGHT,
    STORY_WIDTH,
    banner_text_for_post,
    build_story_image,
)


def _jpeg_blob(color, size=(400, 400)) -> bytes:
    buf = BytesIO()
    Image.new("RGB", size, color).save(buf, format="JPEG")
    return buf.getvalue()


def test_banner_text_for_post():
    assert banner_text_for_post("iPhone 13 128Gb Pink", "23500₽") == "iPhone 13 128Gb Pink - 23500р."
    assert banner_text_for_post("iPhone 13", None) == "iPhone 13"
    assert banner_text_for_post(None, "1000р.") == "1000р."


def test_build_story_image_six_photos_bubble():
    blobs = [_jpeg_blob((30 + i * 30, 80, 160), size=(600, 800)) for i in range(6)]
    logo = _jpeg_blob((200, 0, 0), size=(200, 200))
    data = build_story_image(
        blobs,
        title="iPhone 13 128Gb Pink",
        price="23500р.",
        brand_name="appleshop43",
        logo_blob=logo,
        style="bubble",
    )
    assert data is not None
    img = Image.open(BytesIO(data))
    assert img.size == (STORY_WIDTH, STORY_HEIGHT)
    assert img.format == "JPEG"


def test_build_story_image_six_photos_social():
    blobs = [_jpeg_blob((30 + i * 30, 80, 160), size=(600, 800)) for i in range(6)]
    logo = _jpeg_blob((200, 0, 0), size=(200, 200))
    data = build_story_image(
        blobs,
        title="iPhone 15 Pro 256Gb White Titanium",
        price="53500р.",
        brand_name="appleshop43",
        logo_blob=logo,
        style="social",
    )
    assert data is not None
    img = Image.open(BytesIO(data))
    assert img.size == (STORY_WIDTH, STORY_HEIGHT)
    assert img.format == "JPEG"


def test_build_story_image_requires_photos():
    assert build_story_image([]) is None


def test_font_is_readable_size():
    from app.workers.vk.story_composer import _load_font, font_source

    font = _load_font(56, bold=True)
    im = Image.new("RGB", (1080, 200))
    draw = ImageDraw.Draw(im)
    bbox = draw.textbbox((0, 0), "23500р.", font=font)
    assert bbox[3] - bbox[1] >= 35, (bbox, font_source())
    assert font_source() and "Inter" in font_source()
