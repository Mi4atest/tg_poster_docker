"""Тесты разбора атрибутов телефона для Авито."""
from app.integrations.avito.phone_attributes import (
    build_phone_xml_fields,
    format_kit_set_xml,
    parse_kit_set,
    parse_sim_config,
    strip_avito_condition_lines,
)
from app.integrations.avito.phone_color_catalog import resolve_avito_color_from_catalog


def test_natural_titanium_maps_to_seryy():
    assert resolve_avito_color_from_catalog("iPhone 15 Pro", "Natural Titanium") == "серый"


def test_desert_titanium_maps_to_zolotistyy():
    assert resolve_avito_color_from_catalog("iPhone 16 Pro", "Desert Titanium") == "золотистый"


def test_sim_esim_only_from_text():
    text = "⚠️Поддерживает только eSim\nостальное"
    assert parse_sim_config(text) == "Только eSIM"


def test_sim_two_physical():
    text = "⚠️2 физические Sim\n"
    assert parse_sim_config(text) == "2 SIM"


def test_sim_default_without_markers_even_on_16_pro():
    assert parse_sim_config("", "iPhone 16 Pro") == "SIM + eSIM"
    assert parse_sim_config("обычное описание", "iPhone 16 Pro") == "SIM + eSIM"


def test_battery_percent():
    fields = build_phone_xml_fields(
        post_text="🔋Аккумулятор: 90% (заменена)",
        post_name="iPhone 12",
        title="iPhone 12 Pro 128Gb Silver",
    )
    assert fields["akb"] == 90
    assert fields["color"] == "серебристый"


def test_kit_box_and_cable():
    text = "📦Комплект: коробка и кабель (📦➰)"
    assert parse_kit_set(text) == ["Коробка", "Провод зарядки"]
    assert format_kit_set_xml(parse_kit_set(text)) == "Коробка | Провод зарядки"


def test_kit_phone_and_cable():
    text = "📦Комплект: телефон и кабель (📱➰)"
    assert parse_kit_set(text) == ["Провод зарядки"]


def test_kit_phone_only():
    text = "📦Комплект: телефон (📱)"
    assert parse_kit_set(text) == []


def test_kit_box_and_charger_block():
    text = "📦Комплект: коробка и зарядное устройство (📦🔌)"
    assert parse_kit_set(text) == ["Коробка", "Блок зарядки"]


def test_kit_in_build_phone_xml_fields():
    fields = build_phone_xml_fields(
        post_text="🔥iPhone 12 Pro 256Gb Gold 4237\n📦Комплект: телефон и кабель (📱➰)",
        post_name="iPhone 12 Pro",
        title="iPhone 12 Pro 256Gb Gold 4237",
    )
    assert fields["set"] == "Провод зарядки"


def test_strip_screen_body_from_description():
    d = "Текст\n\nэкран: без дефектов · корпус: мелкие царапины\n\nхвост"
    assert "экран:" not in strip_avito_condition_lines(d)
    assert "хвост" in strip_avito_condition_lines(d)
