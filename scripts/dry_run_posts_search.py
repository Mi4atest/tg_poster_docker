"""Сухой прогон поиска постов в архиве (read-only, без HTTP)."""
from __future__ import annotations

import sys
import time

from app.services.archive_service import (
    POST_SEARCH_LIMIT_DEFAULT,
    POST_SEARCH_LIMIT_MAX,
    fetch_posts_search,
    parse_search_date,
)


def _print_date_parse(samples: list[str]) -> None:
    print("=== parse_search_date ===")
    for q in samples:
        parts = parse_search_date(q)
        print(
            f"  {q!r}: date={parts.is_date_search} "
            f"y={parts.year} m={parts.month} d={parts.day}"
        )


def _run_search(query: str, *, limit: int = POST_SEARCH_LIMIT_DEFAULT) -> None:
    print(f"\n=== search {query!r} (limit={limit}, max={POST_SEARCH_LIMIT_MAX}) ===")
    t0 = time.perf_counter()
    try:
        rows = fetch_posts_search(query, limit=limit)
    except Exception as exc:
        print(f"  ERROR: {exc}")
        return
    elapsed_ms = (time.perf_counter() - t0) * 1000
    print(f"  rows={len(rows)} elapsed_ms={elapsed_ms:.1f}")
    for i, row in enumerate(rows[:5], 1):
        name = (row.get("name") or "—")[:60]
        text = (row.get("text") or "")[:80].replace("\n", " ")
        print(f"  {i}. id={row.get('id')} name={name!r} text={text!r}...")
    if len(rows) > 5:
        print(f"  … ещё {len(rows) - 5}")


def main(argv: list[str]) -> int:
    queries = argv[1:] or ["Apple Watch", "iPhone", "2025", "0825"]
    _print_date_parse(["2025", "0825", "250825", "Apple Watch", "9999"])
    for q in queries:
        _run_search(q)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
