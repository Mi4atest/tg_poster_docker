"""Сухой прогон: формирование ссылок публикации/синхронизации + probe API vk.ru.

Не пишет в VK и не меняет БД (кроме чтения).

Запуск:
  docker-compose exec app python -m scripts.dry_run_vk_ru_links
  # или
  docker-compose exec app python /host_project/scripts/dry_run_vk_ru_links.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    from app.utils.vk_urls import (
        VK_API_ORIGIN,
        VK_WEB_ORIGIN,
        api_method_url,
        market_product_url,
        rewrite_vk_com_to_ru,
        story_url,
        wall_post_url,
    )

    print("=== constants ===")
    print(f"VK_WEB_ORIGIN={VK_WEB_ORIGIN}")
    print(f"VK_API_ORIGIN={VK_API_ORIGIN}")

    print("=== builders ===")
    print(market_product_url(129808251, 11608836))
    print(wall_post_url(-129808251, 25327))
    print(story_url(-129808251, 1))
    print(api_method_url("wall.getById"))

    print("=== rewrite samples ===")
    samples = [
        "https://vk.com/market-129808251?w=product-129808251_1",
        "https://www.vk.com/wall-1_2",
        "https://m.vk.com/market-1",
        "https://api.vk.com/method/utils.getServerTime",
        "https://oauth.vk.com/authorize",
        "https://id.vk.com/about",
        "https://dev.vk.com/",
        "vk.com/market-1?w=product-1_2",
        "https://vk.ru/already",
    ]
    for s in samples:
        print(f"  {s!r} → {rewrite_vk_com_to_ru(s)!r}")

    print("=== publisher/sync import smoke ===")
    from app.utils.vk_market_sync import _vk_item_to_product_dict
    from app.utils.vk_channel_price_formatter import _market_url

    fake_item = {
        "id": 99,
        "owner_id": -129808251,
        "title": "Test",
        "price": {"amount": 10000},
    }
    # group id from settings may fail — wrap
    try:
        d = _vk_item_to_product_dict(fake_item, "test", 1)
        link = d.get("vk_product_link")
        print(f"sync_link={link}")
        assert link and "vk.ru" in link and "vk.com" not in link
    except Exception as exc:
        print(f"sync builder skipped/failed: {exc}")
        # still show expected shape
        print(f"expected={market_product_url(129808251, 99, owner_id=129808251)}")

    mu = _market_url(
        {"vk_product_link": "https://vk.com/market-1?w=product-1_2"},
        group_id=1,
    )
    print(f"channel_price_link_rewrite={mu}")
    assert mu and "vk.ru" in mu and "vk.com" not in mu
    mu2 = _market_url({"vk_product_id": 2}, group_id=129808251)
    print(f"channel_price_link_build={mu2}")
    assert mu2 and "vk.ru" in mu2

    print("=== API probe api.vk.ru vs api.vk.com ===")
    import requests

    for host in ("api.vk.ru", "api.vk.com"):
        url = f"https://{host}/method/utils.getServerTime"
        try:
            r = requests.get(url, params={"v": "5.199"}, timeout=10)
            print(f"{host}: status={r.status_code} body={r.text[:120]}")
        except Exception as exc:
            print(f"{host}: ERROR {exc}")

    print("=== authenticated market/wall dry-read (no writes) ===")
    try:
        from app.utils.vk_client import (
            get_community_vk_session,
            get_market_vk_session,
            resolved_vk_group_id_int,
        )

        gid = resolved_vk_group_id_int()
        print(f"group_id={gid}")
        cvk = get_community_vk_session().get_api()
        # wall.get count=1 — read only
        wall = cvk.wall.get(owner_id=-gid, count=1)
        items = wall.get("items") or []
        if items:
            pid = items[0]["id"]
            print(f"wall_sample_link={wall_post_url(-gid, pid)}")
        else:
            print("wall empty")
        mvk = get_market_vk_session().get_api()
        market = mvk.market.get(owner_id=-gid, count=1)
        m_items = market.get("items") or []
        if m_items:
            mid = m_items[0]["id"]
            print(f"market_sample_link={market_product_url(gid, mid)}")
            # sync dict shape
            d2 = _vk_item_to_product_dict(m_items[0], "dry", 0)
            print(f"sync_from_live={d2.get('vk_product_link')}")
            assert "vk.ru" in d2["vk_product_link"]
        else:
            print("market empty")
    except Exception as exc:
        print(f"auth dry-read failed: {exc}")

    print("=== channel price dry-run (links) ===")
    try:
        from app.utils.vk_channel_price_formatter import (
            build_vk_channel_price,
            fetch_channel_price_products,
            _market_url,
        )
        from app.utils.vk_channel_price_template import (
            DEFAULT_CANONICAL_PRICE,
            parse_price_list_template,
        )

        tpl = parse_price_list_template(DEFAULT_CANONICAL_PRICE)
        products = fetch_channel_price_products()
        link_samples = [
            _market_url(p)
            for p in products
            if p.get("vk_product_link") or p.get("vk_product_id")
        ][:5]
        print(f"db_link_samples={link_samples}")
        if any(u and "vk.com" in u for u in link_samples):
            print("WARN: vk.com still in DB-derived market urls")
            return 2
        rendered = build_vk_channel_price(
            template=tpl,
            products=products[:50],
            with_links=True,
            split_if_needed=True,
        )
        com_in_text = "vk.com/" in rendered.text.lower() or "://vk.com" in rendered.text.lower()
        ru_in_href = any(
            (u or "").startswith("https://vk.ru/") for u in link_samples if u
        )
        print(
            json.dumps(
                {
                    "products": len(products),
                    "char_len": rendered.stats.get("char_len"),
                    "with_vk_link": rendered.stats.get("with_vk_link"),
                    "has_vk_com_in_text": com_in_text,
                    "db_links_on_vk_ru": ru_in_href,
                },
                ensure_ascii=False,
            )
        )
        if com_in_text:
            print("WARN: vk.com still present in rendered price text")
            return 2
    except Exception as exc:
        print(f"channel price dry-run failed: {exc}")

    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
