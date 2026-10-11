#!/usr/bin/env python3
"""Проверка custom emoji через Bot API без публикации в основной канал.

Что делает:
1) getCustomEmojiStickers — валидны ли id
2) sendMessage в личку (--chat-id или первый ALLOWED_USER_IDS)
3) опционально --channel <id> — тест в тестовый канал (НЕ основной)

Интерпретация:
- в ответе Message.entities есть type=custom_emoji → Telegram принял кастомные эмодзи
- entities пустые / только обычный emoji → для этого типа чата боту нельзя
  (для канала часто нужен Fragment username; Premium владельца покрывает
   private/group/supergroup по Bot API 9.4)

Пример:
  python scripts/probe_custom_emoji.py
  python scripts/probe_custom_emoji.py --chat-id 5513789802
  python scripts/probe_custom_emoji.py --channel @my_test_channel
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env", override=True)

from aiogram import Bot
from aiogram.enums import ParseMode

# Порядок из Swiftgram JSON пользователя: Telegram, Instagram, VK, Avito
DEFAULT_EMOJI = {
    "telegram": "5330237710655306682",
    "instagram": "5319160079465857105",
    "vk": "5334853932915114338",
    "avito": "5467625575491334149",
}

# alt должен совпадать с documentAttributeCustomEmoji.alt из пака
# (уточняется через getCustomEmojiStickers; дефолты — из первого ответа API)
ALT = {
    "telegram": "📱",
    "instagram": "📱",
    "vk": "📱",
    "avito": "🚚",
}


def _html_custom(emoji: str, custom_id: str) -> str:
    return f'<tg-emoji emoji-id="{custom_id}">{emoji}</tg-emoji>'


def _build_probe_text() -> str:
    lines = ["Проверка custom emoji (не основной канал)", ""]
    for key in ("telegram", "instagram", "vk", "avito"):
        label = {"telegram": "Телеграм", "instagram": "Инстаграм", "vk": "ВКонтакте", "avito": "Авито"}[key]
        lines.append(f"{_html_custom(ALT[key], DEFAULT_EMOJI[key])} {label}")
    return "\n".join(lines)


def _entities_summary(message) -> list[dict]:
    out = []
    for e in message.entities or []:
        etype = e.type.value if hasattr(e.type, "value") else str(e.type)
        item = {"type": etype, "offset": e.offset, "length": e.length}
        if getattr(e, "custom_emoji_id", None):
            item["custom_emoji_id"] = e.custom_emoji_id
        out.append(item)
    return out


async def probe_target(bot: Bot, chat_id: str | int, label: str) -> bool:
    text = _build_probe_text()
    print(f"\n=== send → {label} ({chat_id}) ===")
    try:
        msg = await bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
    except Exception as exc:
        print(f"FAIL send: {exc}")
        return False

    entities = _entities_summary(msg)
    custom = [e for e in entities if e["type"] == "custom_emoji" or e.get("custom_emoji_id")]
    print(f"message_id: {msg.message_id}")
    print(f"text: {msg.text!r}")
    print(f"entities ({len(entities)}): {entities}")
    if len(custom) >= 4:
        print("OK: все 4 custom_emoji приняты API в этом чате")
        return True
    if custom:
        print(f"PARTIAL: принято {len(custom)}/4 custom_emoji")
        return False
    print(
        "FAIL: custom_emoji в ответе нет — Telegram, скорее всего, "
        "заменил их на обычные emoji (нет права для этого типа чата)"
    )
    return False


async def main() -> int:
    parser = argparse.ArgumentParser(description="Probe Telegram custom emoji for this bot")
    parser.add_argument(
        "--chat-id",
        default="",
        help="Личка/группа для теста (по умолчанию первый ALLOWED_USER_IDS)",
    )
    parser.add_argument(
        "--channel",
        default="",
        help="Опционально: тестовый канал (@name или -100...). Основной канал не трогаем",
    )
    parser.add_argument(
        "--skip-dm",
        action="store_true",
        help="Не слать в личку, только validate + --channel",
    )
    args = parser.parse_args()

    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        print("FAIL: TELEGRAM_BOT_TOKEN не задан")
        return 1

    allowed = [x.strip() for x in os.getenv("ALLOWED_USER_IDS", "").split(",") if x.strip()]
    chat_id = args.chat_id.strip() or (allowed[0] if allowed else "")
    if not args.skip_dm and not chat_id:
        print("FAIL: нет --chat-id и ALLOWED_USER_IDS пуст")
        return 1

    bot = Bot(token=token)
    ok_any = False
    try:
        me = await bot.get_me()
        print(f"bot: @{me.username} id={me.id}")

        ids = list(DEFAULT_EMOJI.values())
        print("\n=== getCustomEmojiStickers ===")
        stickers = await bot.get_custom_emoji_stickers(custom_emoji_ids=ids)
        by_id = {s.custom_emoji_id: s for s in stickers}
        for key, eid in DEFAULT_EMOJI.items():
            st = by_id.get(eid)
            if not st:
                print(f"  {key}: {eid} — НЕ найден в API")
                continue
            # Обновляем ALT под фактический emoji из API (важно для entity)
            if st.emoji:
                ALT[key] = st.emoji
            print(
                f"  {key}: {eid} ok"
                f" alt={st.emoji!r}"
                f" set={getattr(st, 'set_name', None)!r}"
            )
        if len(by_id) < len(ids):
            print(f"WARN: найдено {len(by_id)}/{len(ids)} стикеров — проверьте fileId")

        if not args.skip_dm:
            ok_any = await probe_target(bot, chat_id, "private/DM") or ok_any

        if args.channel.strip():
            ok_any = await probe_target(bot, args.channel.strip(), "channel") or ok_any
        else:
            print(
                "\n(подсказка) Для проверки именно канала создайте тестовый канал, "
                "добавьте бота админом и запустите:\n"
                "  python scripts/probe_custom_emoji.py --skip-dm --channel @your_test_channel"
            )
    finally:
        await bot.session.close()

    print("\nИтог:")
    print(
        "- Успех в личке = Premium владельца работает для private "
        "(Bot API 9.4). Это НЕ гарантия для канала."
    )
    print(
        "- Успех в тестовом канале = можно внедрять в подпись постов канала."
    )
    print(
        "- Провал в канале при успехе в личке = нужен Fragment username у бота "
        "(или другой путь для channel custom emoji)."
    )
    return 0 if ok_any else 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
