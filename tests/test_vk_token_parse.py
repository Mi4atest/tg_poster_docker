from app.utils.vk_token_parse import extract_vk_access_token


def test_extract_from_full_blank_url():
    raw = (
        "https://oauth.vk.ru/blank.html#access_token=vk1.a.ABC123xyz"
        "&expires_in=86400&user_id=393964690"
    )
    assert extract_vk_access_token(raw) == "vk1.a.ABC123xyz"


def test_extract_from_oauth_vk_com():
    raw = (
        "https://oauth.vk.com/blank.html#access_token=vk1.a.TOKEN"
        "&expires_in=86400&user_id=1"
    )
    assert extract_vk_access_token(raw) == "vk1.a.TOKEN"


def test_extract_from_fragment_only():
    raw = "access_token=vk1.a.ONLY&expires_in=86400&user_id=1"
    assert extract_vk_access_token(raw) == "vk1.a.ONLY"


def test_bare_token_unchanged():
    assert extract_vk_access_token("vk1.a.BARE_TOKEN") == "vk1.a.BARE_TOKEN"


def test_empty():
    assert extract_vk_access_token("  ") == ""
