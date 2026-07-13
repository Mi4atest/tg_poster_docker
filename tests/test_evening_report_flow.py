"""Unit tests for evening report text building."""
from app.bot.utils.evening_report_flow import build_report_text, calc_final_cash, parse_nf


def test_build_report_user_example():
    draft = {
        "notes_text": "куплен 15 про 128 натурал (6730) за 27000 (разбита крышка)",
        "morning_cash": 332500,
        "day_cash": 300600,
        "bn": 2600,
        "buybacks": 30000,
        "extra_items": [
            {"name": "тонер", "amount": 600, "kind": "expense"},
            {"name": "переводом", "amount": 850, "kind": "expense"},
        ],
        "nf_primary": 297000,
        "nf_secondary": 3600,
    }
    assert calc_final_cash(draft) == 599050
    text = build_report_text(draft)
    assert "бн 2600" in text
    assert "в кассе 599050" in text
    assert "нф 297000 (3600)" in text
    assert "опт" not in text


def test_optional_fields_omitted():
    draft = {
        "morning_cash": 100,
        "day_cash": 200,
        "extra_items": [],
    }
    text = build_report_text(draft)
    assert "бн" not in text
    assert "в кассе 300" in text


def test_parse_nf():
    assert parse_nf("297000 3600") == (297000.0, 3600.0)
    assert parse_nf("297000 (3600)") == (297000.0, 3600.0)
