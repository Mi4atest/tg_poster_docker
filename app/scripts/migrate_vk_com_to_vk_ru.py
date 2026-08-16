"""Миграция пользовательских ссылок VK: vk.com → vk.ru.

Безопасно: только колонки со ссылками + JSON app_settings.config.
По умолчанию — dry-run (без записи).

Запуск:
  docker-compose exec app python -m app.scripts.migrate_vk_com_to_vk_ru
  docker-compose exec app python -m app.scripts.migrate_vk_com_to_vk_ru --apply
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

root = Path(__file__).resolve().parent.parent.parent
if str(root) not in sys.path:
    sys.path.insert(0, str(root))

from sqlalchemy import text

from app.db.database import SessionLocal
from app.utils.vk_urls import rewrite_vk_com_to_ru


COLUMN_TARGETS: List[Tuple[str, str]] = [
    ("products", "vk_product_link"),
    ("posts", "vk_post_link"),
    ("stories", "post_link"),
]


def _rewrite_json(value: Any) -> Any:
    if isinstance(value, str):
        return rewrite_vk_com_to_ru(value)
    if isinstance(value, list):
        return [_rewrite_json(v) for v in value]
    if isinstance(value, dict):
        return {k: _rewrite_json(v) for k, v in value.items()}
    return value


def _count_column(db, table: str, column: str) -> int:
    row = db.execute(
        text(
            f"SELECT COUNT(*) AS c FROM {table} "
            f"WHERE {column} IS NOT NULL AND {column} ILIKE '%vk.com%'"
        )
    ).mappings().first()
    return int(row["c"] if row else 0)


def _sample_column(db, table: str, column: str, limit: int = 3) -> List[str]:
    rows = db.execute(
        text(
            f"SELECT {column} AS v FROM {table} "
            f"WHERE {column} IS NOT NULL AND {column} ILIKE '%vk.com%' "
            f"LIMIT :lim"
        ),
        {"lim": limit},
    ).mappings().all()
    return [str(r["v"]) for r in rows]


def _migrate_column(db, table: str, column: str, *, apply: bool) -> int:
    before = _count_column(db, table, column)
    if before == 0:
        return 0
    if not apply:
        return before
    # REPLACE на уровне SQL быстрее; дополнительно нормализуем www/m через Python-батч не нужен —
    # у нас почти все https://vk.com/...
    db.execute(
        text(
            f"UPDATE {table} SET {column} = REPLACE({column}, '://www.vk.com', '://vk.ru') "
            f"WHERE {column} ILIKE '%://www.vk.com%'"
        )
    )
    db.execute(
        text(
            f"UPDATE {table} SET {column} = REPLACE({column}, '://m.vk.com', '://m.vk.ru') "
            f"WHERE {column} ILIKE '%://m.vk.com%'"
        )
    )
    db.execute(
        text(
            f"UPDATE {table} SET {column} = REPLACE({column}, '://vk.com', '://vk.ru') "
            f"WHERE {column} ILIKE '%://vk.com%'"
        )
    )
    # голые vk.com/... без схемы
    db.execute(
        text(
            f"UPDATE {table} SET {column} = REPLACE({column}, 'vk.com/', 'vk.ru/') "
            f"WHERE {column} ILIKE 'vk.com/%' OR {column} LIKE 'vk.com/%'"
        )
    )
    return before


def _migrate_app_settings(db, *, apply: bool) -> Tuple[int, List[str]]:
    row = db.execute(text("SELECT id, config FROM app_settings LIMIT 1")).mappings().first()
    if not row:
        return 0, []
    cfg = row["config"]
    if isinstance(cfg, str):
        cfg = json.loads(cfg)
    raw = json.dumps(cfg, ensure_ascii=False)
    if "vk.com" not in raw.lower():
        return 0, []
    samples = []
    # собрать примеры строк
    def _collect(obj: Any) -> None:
        if isinstance(obj, str) and "vk.com" in obj.lower():
            samples.append(obj)
        elif isinstance(obj, dict):
            for v in obj.values():
                _collect(v)
        elif isinstance(obj, list):
            for v in obj:
                _collect(v)

    _collect(cfg)
    if not apply:
        return len(samples), samples[:5]
    new_cfg = _rewrite_json(cfg)
    db.execute(
        text("UPDATE app_settings SET config = CAST(:cfg AS json), updated_at = NOW() WHERE id = :id"),
        {"cfg": json.dumps(new_cfg, ensure_ascii=False), "id": row["id"]},
    )
    return len(samples), samples[:5]


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Migrate VK links vk.com → vk.ru")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Записать изменения (по умолчанию только dry-run)",
    )
    args = parser.parse_args(argv)
    apply = bool(args.apply)
    mode = "APPLY" if apply else "DRY-RUN"
    print(f"=== migrate_vk_com_to_vk_ru [{mode}] ===")

    db = SessionLocal()
    try:
        report: Dict[str, Any] = {"mode": mode, "columns": {}, "app_settings": {}}
        total = 0
        for table, column in COLUMN_TARGETS:
            samples = _sample_column(db, table, column)
            n = _migrate_column(db, table, column, apply=apply)
            total += n
            report["columns"][f"{table}.{column}"] = {
                "matched": n,
                "samples_before": samples,
                "samples_after": [rewrite_vk_com_to_ru(s) for s in samples],
            }
            print(f"{table}.{column}: {n}")
            for s in samples:
                print(f"  {s} → {rewrite_vk_com_to_ru(s)}")

        n_settings, samples_settings = _migrate_app_settings(db, apply=apply)
        total += n_settings
        report["app_settings"] = {
            "matched_strings": n_settings,
            "samples_before": samples_settings,
            "samples_after": [rewrite_vk_com_to_ru(s) for s in samples_settings],
        }
        print(f"app_settings.config strings: {n_settings}")
        for s in samples_settings:
            print(f"  {s} → {rewrite_vk_com_to_ru(s)}")

        if apply:
            db.commit()
            # verify
            left = 0
            for table, column in COLUMN_TARGETS:
                left += _count_column(db, table, column)
            cfg_left = db.execute(
                text(
                    "SELECT COUNT(*) AS c FROM app_settings "
                    "WHERE config::text ILIKE '%vk.com%'"
                )
            ).mappings().first()
            left_settings = int(cfg_left["c"] if cfg_left else 0)
            print(f"commit ok; remaining column matches={left}, app_settings rows={left_settings}")
            report["remaining_column_matches"] = left
            report["remaining_app_settings_rows"] = left_settings
        else:
            db.rollback()
            print(f"dry-run only; would touch ~{total} values (no commit)")

        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        db.rollback()
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
