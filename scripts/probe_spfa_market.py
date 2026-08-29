#!/usr/bin/env python3
"""Живая проверка SPFA + Avito market search (секреты из Настроек / bootstrap .env)."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env", override=True)

from app.integrations.avito.market_search import (
    AvitoMarketBlockedError,
    build_market_web_url,
    fetch_market_listings,
)
from app.integrations.avito.spfa_client import SpfaClient
from app.services.settings_service import get_settings_service
from app.utils.iphone_market_query import parse_iphone_market_query


async def main() -> int:
    settings = get_settings_service()
    settings.bootstrap_from_env_once()
    spfa_key = settings.get_spfa_api_key()
    market_proxy = settings.get_avito_market_proxy()

    if not spfa_key:
        print("FAIL: SPFA API ключ не задан (Настройки → Оценка рынка Avito)")
        return 1

    client = SpfaClient(spfa_key, proxy=market_proxy)
    balance = await client.get_balance()
    print(f"SPFA balance: {balance}")

    query = parse_iphone_market_query("13 mini 128")
    web = build_market_web_url(query)
    api_url = await client.convert_web_url(web)
    print(f"avito-url ok: {api_url[:140]}...")

    if not market_proxy:
        print(
            "WARN: прокси пуст. "
            "Mobile cookies недоступны; desktop cookies с DC IP обычно дают HTTP 439."
        )

    try:
        listings = await fetch_market_listings(query)
    except AvitoMarketBlockedError as exc:
        print(f"BLOCKED: {exc}")
        print("RESULT: SPFA ключ валиден, но без RU-прокси поиск Avito не проходит.")
        return 2
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}")
        return 3

    print(f"OK: listings={len(listings)}")
    for item in listings[:5]:
        print(f" - {item.price_rub} | {item.title[:80]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
