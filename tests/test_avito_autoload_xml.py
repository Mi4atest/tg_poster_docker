"""Тесты XML-фида автозагрузки."""
from app.integrations.avito.autoload_xml import (
    build_feed_xml_for_post,
    build_feed_xml_for_posts,
    build_image_urls,
    build_images_xml,
    sanitize_ad_id,
)
from app.integrations.avito.feed_store import save_feed, load_feed_xml, load_feed_meta


def test_sanitize_ad_id():
    assert sanitize_ad_id("abc-123") == "abc-123"
    assert " " not in sanitize_ad_id("a b c")


def test_build_image_urls():
    urls = build_image_urls(["AgAC123", "AgAC456"], "https://appleshop.ap43.ru")
    assert "https://appleshop.ap43.ru/api/telegram/file/AgAC123" in urls
    assert "|" in urls


def test_build_images_xml():
    xml = build_images_xml(["photo1"], "https://example.com")
    assert "<Images>" in xml
    assert '<Image url="https://example.com/api/telegram/file/photo1"/>' in xml
    assert "ImageUrls" not in xml


def test_build_feed_xml_screen_level_zero_defaults():
    """Уровень 0 в черновике — обязательные ScreenCondition/CaseCondition для автозагрузки."""
    xml = build_feed_xml_for_post(
        post_id="test-post-0",
        post_text="iPhone 13\n💵 45000₽",
        post_name="iPhone",
        photos=[],
        avito_draft={"screen_level": 0, "body_level": 0},
        public_base_url="https://example.com",
        contact_phone="79229228588",
        address="Киров",
    )
    assert "<ScreenCondition>Без дефектов</ScreenCondition>" in xml
    assert "<CaseCondition>Без дефектов</CaseCondition>" in xml


def test_build_feed_xml_contains_set_from_kit_line():
    xml = build_feed_xml_for_post(
        post_id="test-post-kit",
        post_text=(
            "iPhone 12 128Gb Purple 8110\n"
            "💵 13900₽\n"
            "📦Комплект: коробка и кабель (📦➰)"
        ),
        post_name="iPhone 12",
        photos=[],
        avito_draft={"screen_level": 0, "body_level": 0},
        public_base_url="https://example.com",
        contact_phone="79229228588",
        address="Киров",
    )
    assert "<Set>Коробка | Провод зарядки</Set>" in xml


def test_build_feed_xml_contains_fields():
    xml = build_feed_xml_for_post(
        post_id="test-post-1",
        post_text="iPhone 13 mini\n💵 45000₽\nСостояние отличное",
        post_name="iPhone",
        photos=["photo1"],
        avito_draft={"screen_level": 2, "body_level": 3},
        public_base_url="https://example.com",
        contact_phone="79229228588",
        address="Киров",
    )
    assert "<Id>" in xml
    assert "<Category>Телефоны</Category>" in xml
    assert "<Price>45000</Price>" in xml
    assert "<ScreenCondition>1–2 мелкие царапины</ScreenCondition>" in xml
    assert "<CaseCondition>Глубокие царапины</CaseCondition>" in xml
    assert "<Images>" in xml
    assert "iPhone" in xml


def test_build_feed_xml_multiple_ads():
    xml = build_feed_xml_for_posts(
        [
            {"post_id": "p1", "post_text": "Phone A\n50000", "post_name": "A", "photos": [], "avito_draft": {"screen_level": 1, "body_level": 1}},
            {"post_id": "p2", "post_text": "Phone B\n40000", "post_name": "B", "photos": [], "avito_draft": {"screen_level": 2, "body_level": 2}},
        ],
        public_base_url="https://example.com",
        contact_phone="79220000000",
        address="Киров",
    )
    assert xml.count("<Ad>") == 2
    assert "<Id>p1</Id>" in xml or "<Id>" in xml


def test_feed_store_roundtrip(tmp_path, monkeypatch):
    from app.integrations.avito import feed_store

    monkeypatch.setattr(feed_store, "FEED_DIR", tmp_path)
    monkeypatch.setattr(feed_store, "FEED_META_PATH", tmp_path / "current_meta.json")
    monkeypatch.setattr(feed_store, "FEED_XML_PATH", tmp_path / "current.xml")
    save_feed("<Ads></Ads>", "p1", "p1")
    assert load_feed_xml() == "<Ads></Ads>"
    assert load_feed_meta().get("post_id") == "p1"
