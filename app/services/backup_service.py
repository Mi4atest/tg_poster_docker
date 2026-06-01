"""Резервное копирование БД внутри приложения (без хостового cron).

Параметры берутся из меню «Настройки → Бэкап» (БД), токен — из encrypted_secrets.
Запускается планировщиком из app/bot/main.py и кнопкой «Сделать бэкап сейчас».
"""
from __future__ import annotations

import asyncio
import gzip
import logging
import shutil
import socket
from datetime import datetime
from pathlib import Path
from typing import Tuple

import aiohttp

from app.config.settings import BASE_DIR, DATABASE_URL
from app.services.settings_service import get_settings_service

logger = logging.getLogger(__name__)

BACKUP_DIR = BASE_DIR / "backups"
# Telegram ограничивает размер документа бота ~50 МБ
TELEGRAM_MAX_DOCUMENT_BYTES = 50 * 1024 * 1024


async def _send_document(token: str, chat_id: str, path: Path, project: str) -> bool:
    url = f"https://api.telegram.org/bot{token}/sendDocument"
    server_name = socket.gethostname()
    size_mb = path.stat().st_size / (1024 * 1024)
    caption = (
        "📦 <b>Резервная копия базы данных</b>\n"
        f"🔹 <b>Проект:</b> {project}\n"
        f"🔹 <b>Сервер:</b> {server_name}\n"
        f"🔹 <b>Дата:</b> {datetime.now():%d.%m.%Y %H:%M}\n"
        f"🔹 <b>Размер:</b> {size_mb:.2f} МБ"
    )
    try:
        with open(path, "rb") as fh:
            form = aiohttp.FormData()
            form.add_field("chat_id", str(chat_id))
            form.add_field("caption", caption)
            form.add_field("parse_mode", "HTML")
            form.add_field("document", fh, filename=path.name)
            async with aiohttp.ClientSession() as session:
                async with session.post(url, data=form, timeout=aiohttp.ClientTimeout(total=600)) as resp:
                    if resp.status != 200:
                        body = await resp.text()
                        logger.error("Telegram sendDocument %s: %s", resp.status, body[:300])
                        return False
                    return True
    except Exception as exc:
        logger.error("Ошибка отправки бэкапа в Telegram: %s", exc)
        return False


async def _pg_dump(sql_path: Path) -> Tuple[bool, str]:
    """Дамп БД через pg_dump (postgresql-client должен быть установлен в образе)."""
    with open(sql_path, "wb") as out:
        proc = await asyncio.create_subprocess_exec(
            "pg_dump",
            "--dbname", DATABASE_URL,
            "--no-owner",
            "--no-privileges",
            stdout=out,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
    if proc.returncode != 0:
        return False, (stderr or b"").decode("utf-8", "replace")[:300]
    if not sql_path.exists() or sql_path.stat().st_size < 1000:
        return False, "Дамп пуст или слишком мал"
    return True, ""


def _cleanup_old(project: str, keep_days: int) -> None:
    try:
        cutoff = datetime.now().timestamp() - keep_days * 86400
        for f in BACKUP_DIR.glob(f"{project}_backup_*.gz"):
            try:
                if f.stat().st_mtime < cutoff:
                    f.unlink()
            except OSError:
                pass
    except Exception as exc:
        logger.warning("Очистка старых бэкапов не удалась: %s", exc)


async def run_backup(reason: str = "manual") -> Tuple[bool, str]:
    """Создаёт бэкап БД, отправляет в Telegram. Возвращает (успех, сообщение)."""
    svc = get_settings_service()
    cfg = svc.get_backup_config()
    token = svc.get_backup_bot_token()
    chat_id = cfg["chat_id"]
    project = cfg["project_name"] or "tg_poster"

    if not token or not chat_id:
        return False, "Не заданы токен бота или chat_id бэкапа (Настройки → Бэкап)"

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    sql_path = BACKUP_DIR / f"{project}_backup_{ts}.sql"
    gz_path = BACKUP_DIR / f"{project}_backup_{ts}.sql.gz"

    logger.info("Бэкап БД (%s): старт", reason)
    ok, err = await _pg_dump(sql_path)
    if not ok:
        sql_path.unlink(missing_ok=True)
        return False, f"pg_dump: {err}"

    try:
        with open(sql_path, "rb") as fi, gzip.open(gz_path, "wb") as fo:
            shutil.copyfileobj(fi, fo)
    finally:
        sql_path.unlink(missing_ok=True)

    sent = await _send_document(token, chat_id, gz_path, project)

    if cfg.get("media"):
        media_dir = BASE_DIR / "media"
        if media_dir.exists():
            media_gz = BACKUP_DIR / f"{project}_media_{ts}.tar.gz"
            try:
                await asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: shutil.make_archive(str(media_gz)[:-7], "gztar", root_dir=str(BASE_DIR), base_dir="media"),
                )
                if media_gz.exists() and media_gz.stat().st_size < TELEGRAM_MAX_DOCUMENT_BYTES:
                    await _send_document(token, chat_id, media_gz, f"{project} (media)")
                else:
                    logger.warning("Архив media слишком большой для Telegram, оставлен локально")
            except Exception as exc:
                logger.warning("Бэкап media не удался: %s", exc)

    _cleanup_old(project, int(cfg.get("keep_days", 30)))

    if not sent:
        return False, "Бэкап создан локально, но не отправлен в Telegram (проверьте токен/chat_id)"
    return True, "Бэкап создан и отправлен в Telegram"


async def backup_scheduler_loop() -> None:
    """Фоновый цикл: раз в сутки в заданное время запускает бэкап, если включён."""
    logger.info("Backup scheduler started")
    last_run_date = None
    while True:
        try:
            cfg = get_settings_service().get_backup_config()
            if cfg.get("enabled"):
                now = datetime.now()
                if (
                    now.hour == int(cfg.get("hour", 3))
                    and now.minute == int(cfg.get("minute", 0))
                    and last_run_date != now.date()
                ):
                    last_run_date = now.date()
                    ok, msg = await run_backup("scheduled")
                    logger.info("Авто-бэкап: ok=%s, %s", ok, msg)
        except Exception as exc:
            logger.error("backup_scheduler_loop error: %s", exc)
        await asyncio.sleep(30)
