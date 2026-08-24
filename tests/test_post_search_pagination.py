"""Пагинация и контекст поиска архива постов (без БД)."""

from app.bot.handlers.post_management import (
    POST_SEARCH_PER_PAGE,
    _clamp_search_page,
    _clear_post_search_context,
    _post_search_results_keyboard,
)
from app.bot.keyboards.main_keyboard import get_post_actions_keyboard, post_actions_kb_for_user


def test_clamp_search_page():
    assert _clamp_search_page(0, 0) == 0
    assert _clamp_search_page(5, 25) == 2  # 25 items / 10 = pages 0..2
    assert _clamp_search_page(-1, 25) == 0
    assert _clamp_search_page(99, 25) == 2


def test_post_search_keyboard_paginates_ten():
    posts = [{"id": f"p{i}", "name": f"Post {i}"} for i in range(25)]
    kb = _post_search_results_keyboard(posts, page=0, per_page=POST_SEARCH_PER_PAGE)
    rows = kb.inline_keyboard
    # 10 post buttons + nav + 3 control rows
    assert len(rows) == 10 + 1 + 3
    assert rows[0][0].callback_data == "view_post_p0"
    assert rows[9][0].callback_data == "view_post_p9"
    nav = rows[10]
    assert any(b.callback_data == "psearch_posts_1" for b in nav)
    assert not any(b.callback_data == "psearch_posts_0" for b in nav)

    kb2 = _post_search_results_keyboard(posts, page=2, per_page=POST_SEARCH_PER_PAGE)
    rows2 = kb2.inline_keyboard
    assert rows2[0][0].callback_data == "view_post_p20"
    assert len([r for r in rows2 if r and r[0].callback_data.startswith("view_post_")]) == 5
    nav2 = next(r for r in rows2 if any("psearch_posts_" in (b.callback_data or "") for b in r))
    assert any(b.callback_data == "psearch_posts_1" for b in nav2)
    assert not any(b.callback_data == "psearch_posts_3" for b in nav2)


def test_clear_post_search_context():
    ud = {
        "from_post_search": True,
        "post_search_query": "Apple",
        "post_search_page": 2,
        "post_search_results": [{"id": "1"}],
        "in_archive": True,
    }
    _clear_post_search_context(ud)
    assert ud["from_post_search"] is False
    assert "post_search_query" not in ud
    assert ud["in_archive"] is True


def test_back_button_label_depends_on_context():
    kb_search = post_actions_kb_for_user({"from_post_search": True, "in_archive": True})
    assert kb_search.inline_keyboard[-1][0].text == "⬅️ Назад к поиску"
    assert kb_search.inline_keyboard[-1][0].callback_data == "back_to_archive"

    kb_arch = post_actions_kb_for_user({"in_archive": True, "from_post_search": False})
    assert kb_arch.inline_keyboard[-1][0].text == "⬅️ Назад к архиву"

    kb_draft = get_post_actions_keyboard(from_archive=False, from_search=False)
    assert kb_draft.inline_keyboard[-1][0].text == "⬅️ Назад к черновикам"
    assert kb_draft.inline_keyboard[-1][0].callback_data == "back_to_posts"
