"""Сухой прогон генерации прайса VK-канала: длина, матчи, ссылки."""
from __future__ import annotations

import json
import sys

from app.utils.vk_channel_price_formatter import (
    SAFE_MESSAGE_BUDGET,
    VK_MESSAGE_MAX_LENGTH,
    build_vk_channel_price,
    fetch_channel_price_products,
)
from app.utils.vk_channel_price_template import (
    DEFAULT_CANONICAL_PRICE,
    ensure_default_template_file,
    parse_price_list_template,
    save_template_to_file,
)


def main() -> int:
    tpl = parse_price_list_template(DEFAULT_CANONICAL_PRICE)
    path = ensure_default_template_file()
    save_template_to_file(tpl, path)
    print(f"template_file={path}")
    print(f"sections={len(tpl.sections)} slots={len(tpl.all_slots())}")
    for s in tpl.sections:
        print(f"  - {s.id}: {s.title!r} slots={len(s.slots)}")

    products = []
    db_ok = False
    try:
        products = fetch_channel_price_products()
        db_ok = True
    except Exception as exc:
        print(f"DB unavailable ({exc}); measuring template-only empty overlay")

    rendered = build_vk_channel_price(
        template=tpl,
        products=products,
        with_links=True,
        split_if_needed=True,
    )
    print("--- stats ---")
    print(json.dumps(rendered.stats, ensure_ascii=False, indent=2))
    print(f"VK_MESSAGE_MAX_LENGTH={VK_MESSAGE_MAX_LENGTH}")
    print(f"SAFE_MESSAGE_BUDGET={SAFE_MESSAGE_BUDGET}")
    print(f"fits_single={rendered.stats.get('char_len', 0) <= SAFE_MESSAGE_BUDGET}")
    print(f"parts={len(rendered.parts)}")
    if len(rendered.parts) > 1:
        for i, p in enumerate(rendered.parts, 1):
            print(f"  part[{i}] len={len(p)}")
    print("--- preview (first 1200 chars) ---")
    print(rendered.text[:1200])
    print("...")
    print(f"db_products={len(products)} db_ok={db_ok}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
