#!/usr/bin/env python3
"""
Скачать phone_catalog.xml с Авито (для валидации допустимых Color).

iphone_color_map.json — ручной каталог EN→RU, этот скрипт его НЕ перезаписывает.

  python3 scripts/refresh_avito_phone_colors.py
  docker exec tg_poster_app python3 /app/scripts/refresh_avito_phone_colors.py
"""
from __future__ import annotations

import argparse
import asyncio
import ssl
import sys
from pathlib import Path

import aiohttp

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.integrations.avito.phone_color_catalog import load_apple_model_colors  # noqa: E402

CATALOG_URL = "https://www.avito.ru/web/1/catalogs/content/feed/phone_catalog.xml"
CATALOG_PATH = ROOT / "media" / "avito_feed" / "phone_catalog.xml"


async def download_catalog() -> str:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    async with aiohttp.ClientSession() as session:
        async with session.get(
            CATALOG_URL,
            headers={"User-Agent": "Mozilla/5.0 (compatible; tg-poster/1.0)"},
            ssl=ctx,
            timeout=120,
        ) as resp:
            text = await resp.text()
            if resp.status != 200:
                raise RuntimeError(f"HTTP {resp.status}: {text[:300]}")
            return text


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--local-only",
        action="store_true",
        help="Не скачивать; только проверить локальный phone_catalog.xml",
    )
    args = parser.parse_args()

    if not args.local_only:
        print("Downloading catalog…")
        xml = await download_catalog()
        CATALOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CATALOG_PATH.write_text(xml, encoding="utf-8")
        print(f"Saved {CATALOG_PATH} ({len(xml)} bytes)")
    elif not CATALOG_PATH.is_file():
        raise SystemExit(f"Нет файла {CATALOG_PATH}")

    load_apple_model_colors.cache_clear()
    models = load_apple_model_colors()
    print(f"Apple iPhone models in XML: {len(models)}")


if __name__ == "__main__":
    asyncio.run(main())
