"""Archive post card: two <pre> copy blocks (title + description)."""

from app.bot.utils.post_edit_flow import format_archive_post_card, format_post_card_for_user
from app.utils.product_parser import extract_product_description, extract_product_name

SAMPLE_POST_TEXT = """🔥iPhone 17 Pro Max 512Gb Deep Blue 9619 (Б/у, Оригинал)🔥

💵Цена: 114900р.

📅Активирован 11.02.2026
🆕Состояние нового телефона
📦Комплект: коробка и кабель (📦➰)
🔋Аккумулятор: 100% (100 циклов)
📱IMEI 356722736619619 (9619)

——————————————
🍏Гарантия 14 дней
🍏Перенос всех данных
🍏Кредит от 10 банков
🍏Наличными. Картой, QR +10%
🍏Доставка по запросу
🍏Trade-In на Apple
——————————————
📍Мы находимся по адресу:
Г. Киров, ул. Горького, 5А, ТРЦ «Джем Сити», 1 этаж («торговый остров» рядом с Palmoda).
⏰ Работаем без выходных:
с 10.00 до 22.00.
"""


def test_extract_product_name_strips_emoji_and_condition():
    name = extract_product_name(SAMPLE_POST_TEXT)
    assert name == "iPhone 17 Pro Max 512Gb Deep Blue 9619"


def test_extract_product_description_includes_hours_excludes_price():
    desc = extract_product_description(SAMPLE_POST_TEXT)
    assert desc is not None
    assert "💵" not in desc
    assert "114900" not in desc
    assert "Активирован 11.02.2026" in desc
    assert "IMEI 356722736619619" in desc
    assert "Гарантия 14 дней" in desc
    assert "Горького" in desc
    assert "Работаем без выходных" in desc
    assert "до 22.00" in desc


def test_format_archive_post_card_has_two_pre_blocks():
    post = {
        "name": "iPhone 17 Pro Max 512Gb Deep Blue 9619",
        "text": SAMPLE_POST_TEXT,
        "photos": [1, 2, 3, 4, 5, 6],
        "videos": [1],
        "is_published_vk": True,
        "is_published_telegram": True,
        "is_published_instagram": False,
        "is_published_max": False,
        "is_published_avito": False,
    }
    html = format_archive_post_card(post)
    assert html.count("<pre>") == 2
    assert html.count("</pre>") == 2
    assert "<pre>iPhone 17 Pro Max 512Gb Deep Blue 9619</pre>" in html
    assert "💵Цена: 114900р." in html
    # цена вне pre-блоков
    price_idx = html.index("💵Цена")
    first_pre_end = html.index("</pre>")
    second_pre_start = html.index("<pre>", first_pre_end)
    assert first_pre_end < price_idx < second_pre_start
    assert "Работаем без выходных" in html
    assert "📷 6 фото" in html
    assert "📹 1 видео" in html


def test_format_post_card_for_user_archive_vs_drafts():
    post = {
        "name": "Test",
        "text": SAMPLE_POST_TEXT,
        "photos": [],
        "videos": [],
    }
    archive_body, archive_mode = format_post_card_for_user(post, {"in_archive": True})
    assert archive_mode == "HTML"
    assert "<pre>" in archive_body

    draft_body, draft_mode = format_post_card_for_user(post, {})
    assert draft_mode is None
    assert "<pre>" not in draft_body
