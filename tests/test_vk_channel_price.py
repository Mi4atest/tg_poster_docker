"""Тесты парсера ссылок и шаблона прайса VK-канала."""
from app.utils.vk_channel_link import (
    parse_vk_channel_message_link,
    parse_vk_channel_message_links,
)
from app.utils.vk_channel_price_template import (
    DEFAULT_CANONICAL_PRICE,
    parse_price_list_template,
)


def test_parse_vk_channel_link_url():
    ref = parse_vk_channel_message_link(
        "https://vk.ru/im/channels/-235526445?cmid=1"
    )
    assert ref is not None
    assert ref.peer_id == -235526445
    assert ref.cmid == 1


def test_parse_vk_channel_link_bare():
    ref = parse_vk_channel_message_link("-235526445, 1")
    assert ref is not None
    assert ref.peer_id == -235526445
    assert ref.cmid == 1


def test_parse_vk_channel_links_multi():
    refs = parse_vk_channel_message_links(
        "https://vk.ru/im/channels/-235526445?cmid=1\n"
        "https://vk.ru/im/channels/-235526445?cmid=2"
    )
    assert len(refs) == 2
    assert refs[0].cmid == 1
    assert refs[1].cmid == 2


def test_parse_canonical_template_sections():
    tpl = parse_price_list_template(DEFAULT_CANONICAL_PRICE)
    assert len(tpl.sections) == 10
    assert len(tpl.all_slots()) == 103
    ids = [s.id for s in tpl.sections]
    assert ids[0] == "airpods"
    assert ids[-1] == "iphone_new"
    iphone = tpl.sections[-1]
    assert iphone.slots[0].model == "14"
    assert iphone.slots[0].color == "⚫️"
    assert iphone.slots[0].storage == "1+1"


def test_formatter_empty_fits_limit():
    from app.utils.vk_channel_price_formatter import (
        SAFE_MESSAGE_BUDGET,
        build_vk_channel_price,
        build_telegram_channel_price,
    )
    from app.utils.vk_channel_price_template import parse_price_list_template, DEFAULT_CANONICAL_PRICE

    tpl = parse_price_list_template(DEFAULT_CANONICAL_PRICE)
    rendered = build_vk_channel_price(
        template=tpl,
        products=[],
        with_links=False,
        split_if_needed=True,
    )
    assert rendered.stats["char_len"] <= SAFE_MESSAGE_BUDGET
    assert len(rendered.parts) == 1
    assert "AirPods 4" in rendered.text

    tg = build_telegram_channel_price(
        template=tpl,
        products=[],
        with_links=False,
        max_len=4000,
        max_parts=4,
    )
    assert len(tg.parts) >= 1
    assert len(tg.parts) <= 4
    assert all(len(p) <= 4000 for p in tg.parts)
    assert "AirPods 4" in tg.parts[0] or any("AirPods 4" in p for p in tg.parts)
