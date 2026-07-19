"""Сухой прогон пакетного сопоставления цен (без записи в БД)."""
from __future__ import annotations

from app.utils.bulk_price_matcher import MatchStatus, fetch_active_new_products, match_bulk_lines
from app.utils.bulk_price_parser import parse_bulk_price_input, parse_label

SAMPLE = r"""
AirPods 4: 10500 → 10900 (+400₽)
AirPods 4 ANC: 14400 → 14900 (+500₽)
AirPods Pro 2 (USB-C): 13900 → 14500 (+600₽)
AirPods Pro 3: 16900 → 17990 (+1090₽)
AirPods Max 2: 430 → 43090 (+42660₽)
Pencil USB-C: 7500 → 7590 (+90₽)
Pencil 2: 7500 → 7590 (+90₽)
Pencil Pro: 9200 → 9290 (+90₽)
AirTag: 3500 → 3590 (+90₽)
AirTag (4 шт.): 9500 → 9590 (+90₽)
SE2 40 мм, Midnight: 18900 → 18110 (-790₽)
SE2 44 мм, Midnight: 19900 → 19910 (+10₽)
SE3 40 мм, Midnight: 20500 → 20910 (+410₽)
SE3 40 мм, Starlight: 21900 → 21910 (+10₽)
SE3 44 мм, Starlight: 23900 → 23910 (+10₽)
11 42 мм, Black: 29500 → 29901 (+401₽)
11 42 мм, Space Gray: 29500 → 29901 (+401₽)
11 42 мм, Rose: 27900 → 27991 (+91₽)
11 42 мм, Silver: 28900 → 28901 (+1₽)
11 46 мм, Space Gray: 30900 → 30100 (-800₽)
11 46 мм, Rose: 31500 → 31510 (+10₽)
11 46 мм, Silver: 30900 → 30910 (+10₽)
iPad 11 (A16, 2025) 128GB, Blue: 33903 → 33900 (-3₽)
iPad 11 (A16, 2025) 128GB, White: 34933 → 34900 (-33₽)
iPad 11 (A16, 2025) 128GB, Pink: 34903 → 34900 (-3₽)
iPad 11 (A16, 2025) 128GB, Yellow: 33530 → 33700 (+170₽)
iPad Air 11" M4 (2026) 128GB Wi-Fi, Blue: 54900 → 54300 (-600₽)
iPad Air 11" M4 (2026) 128GB Wi-Fi, Starlight: 56900 → 56300 (-600₽)
iPad Air 11" M4 (2026) 128GB Wi-Fi, Purple: 56900 → 56300 (-600₽)
iPad Air 11" M4 (2026) 128GB Wi-Fi, Gray: 55000 → 55100 (+100₽)
13 Pro 128GB (обменка): 37910 → 36900 (-1010₽)
14 Pro 128GB eSim (обменка): 43530 → 43500 (-30₽)
15 128GB (обменка): 10090 → 43333 (+33243₽)
16 256GB (обменка): 299900 → 499020 (+199120₽)
16 Pro 128GB (обменка): 29910 → 434777 (+404867₽)
16 Pro 256GB eSim (обменка): 29910 → 434888 (+404978₽)
16 Pro Max 256GB eSim (обменка): 79910 → 79900 (-10₽)
17 Pro 256GB eSim (обменка): 299110 → 35900 (-263210₽)
17 Pro 512GB eSim (обменка): 299920 → 199989 (-99931₽)
17 Pro Max 512GB eSim (обменка): 299900 → 199989 (-99911₽)
13 Pro 128GB RFB: 40900 → 41300 (+400₽)
14 128GB ⚫️: 44900 → 44100 (-800₽)
14 128GB 🔵: 44900 → 44300 (-600₽)
14 128GB 🟡: 42900 → 42300 (-600₽)
15 128GB ⚫️: 50500 → 50300 (-200₽)
15 128GB 🔵: 50500 → 50300 (-200₽)
16 128GB ⚫️: 57500 → 57100 (-400₽)
16 128GB ⚪️: 56900 → 57100 (+200₽)
16 128GB 🌸: 60900 → 59100 (-1800₽)
16 128GB 🟢: 55500 → 56200 (+700₽)
16 128GB 🔵: 55900 → 57530 (+1630₽)
16e 128GB ⚫️: 44500 → 44300 (-200₽)
16e 128GB ⚪️: 44500 → 44300 (-200₽)
17e 256GB ⚫️ eSim: 52500 → 54910 (+2410₽)
17e 256GB ⚪️ eSim: 52500 → 54910 (+2410₽)
17e 256GB 🌸 eSim: 53900 → 53910 (+10₽)
17e 256GB ⚫️ Sim+eSim: 53500 → 54510 (+1010₽)
17e 256GB ⚪️ Sim+eSim: 53900 → 54510 (+610₽)
17e 256GB 🌸 Sim+eSim: 53900 → 45510 (-8390₽)
17 256GB ⚫️ eSim: 67900 → 71530 (+3630₽)
17 256GB ⚪️ eSim: 69900 → 71530 (+1630₽)
17 256GB 🔵 eSim: 67500 → 70530 (+3030₽)
17 256GB 🟢 eSim: 67500 → 70530 (+3030₽)
17 256GB 🟣 eSim: 69500 → 72530 (+3030₽)
17 256GB ⚫️ Sim+eSim: 68900 → 70530 (+1630₽)
17 256GB ⚪️ Sim+eSim: 68900 → 70530 (+1630₽)
17 256GB 🔵 Sim+eSim: 68500 → 70300 (+1800₽)
17 256GB 🟢 Sim+eSim: 68900 → 70300 (+1400₽)
17 256GB 🟣 Sim+eSim: 70900 → 72390 (+1490₽)
Air 256GB ⚫️ eSim: 70900 → 73930 (+3030₽)
Air 256GB ⚪️ eSim: 71500 → 72903 (+1403₽)
Air 256GB 🟡 eSim: 69900 → 71930 (+2030₽)
Air 256GB 🔵 eSim: 69900 → 72300 (+2400₽)
17 Pro 256GB ⚪️ eSim: 94500 → 91100 (-3400₽)
17 Pro 256GB 🔵 eSim: 87500 → 90100 (+2600₽)
17 Pro 256GB 🟠 eSim: 86900 → 88100 (+1200₽)
17 Pro 256GB ⚪️ Sim+eSim: 96900 → 97910 (+1010₽)
17 Pro 256GB 🔵 Sim+eSim: 96900 → 97911 (+1011₽)
17 Pro 256GB 🟠 Sim+eSim: 96900 → 97910 (+1010₽)
17 Pro 512GB ⚪️ eSim: 104900 → 106100 (+1200₽)
17 Pro 512GB 🔵 eSim: 105500 → 107100 (+1600₽)
17 Pro 512GB 🟠 eSim: 105500 → 106100 (+600₽)
17 Pro 512GB ⚪️ Sim+eSim: 119900 → 117100 (-2800₽)
17 Pro 512GB 🔵 Sim+eSim: 115900 → 115100 (-800₽)
17 Pro 512GB 🟠 Sim+eSim: 113500 → 115510 (+2010₽)
17 Pro Max 256GB ⚪️ eSim: 96900 → 97100 (+200₽)
17 Pro Max 256GB 🔵 eSim: 95500 → 97510 (+2010₽)
17 Pro Max 256GB 🟠 eSim: 94900 → 96910 (+2010₽)
17 Pro Max 256GB ⚪️ Sim+eSim — 10911@: 108900 → 10911 (-97989₽)
17 Pro Max 256GB 🔵 Sim+eSim: 106900 → 109510 (+2610₽)
17 Pro Max 256GB 🟠 Sim+eSim: 10650 → 107910 (+97260₽)
17 Pro Max 512GB ⚪️ eSim: 114500 → 116910(+2410₽)
17 Pro Max 512GB 🔵 eSim: 111900 → 114510 (+2610₽)
17 Pro Max 512GB 🟠 eSim: 112900 → 114500 (+1600₽)
17 Pro Max 512GB ⚪️ Sim+eSim: 127900 → 127910 (+10₽)
17 Pro Max 512GB 🔵 Sim+eSim: 122500 → 123510 (+1010₽)
17 Pro Max 512GB 🟠 Sim+eSim: 123900 → 126910 (+3010₽)
18 256GB ⚪️ eSim: Новый элемент, цена: 97900₽
"""


def main() -> None:
    lines, new_items, skipped = parse_bulk_price_input(SAMPLE)
    products = fetch_active_new_products()
    results = match_bulk_lines(lines, products)

    by_status: dict[str, list] = {}
    for r in results:
        by_status.setdefault(r.status.value, []).append(r)

    print(f"CATALOG_SIZE={len(products)}")
    print(f"PARSED_LINES={len(lines)} NEW_ITEMS={len(new_items)} SKIPPED={len(skipped)}")
    if skipped:
        print("SKIPPED:", skipped)
    print("STATUS_COUNTS:")
    for k, v in sorted(by_status.items(), key=lambda x: -len(x[1])):
        print(f"  {k}: {len(v)}")
    print()

    for status in (
        MatchStatus.MATCHED.value,
        MatchStatus.PRICE_MISMATCH.value,
        MatchStatus.AMBIGUOUS.value,
        MatchStatus.NOT_FOUND.value,
    ):
        items = by_status.get(status, [])
        print(f"=== {status.upper()} ({len(items)}) ===")
        for r in items:
            p = r.parsed
            key = (
                f"{p.category}|{p.model}|mem={p.memory}|col={p.color}"
                f"|st={p.storage}|sz={p.size}|cond={p.condition}"
            )
            extra = ""
            if r.product_id:
                extra = f" -> #{r.product_id} {r.product_name!r} db={r.db_price_rub}"
            if r.price_change:
                extra += f" level={r.price_change.level.value}"
            if r.candidates:
                extra += f" candidates={[c.get('name') for c in r.candidates]}"
            print(f"  [{r.status.value}] {r.line.raw_label}")
            print(
                f"      key={key} old={r.line.old_rub}→{r.line.new_rub}{extra}"
            )
        print()

    print("=== NEW_ITEMS ===")
    for n in new_items:
        pl = parse_label(n.raw_label)
        print(
            f"  {n.raw_label} → {n.new_rub} "
            f"parsed={pl.category}|{pl.model}|{pl.memory}|{pl.color}|{pl.storage}"
        )


if __name__ == "__main__":
    main()
