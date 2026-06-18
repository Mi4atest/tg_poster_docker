"""Обновление проекта с GitHub (git pull + пересборка app).

Запускается из бота через отдельный контейнер-обновлятор, чтобы перезапуск app
не обрывал сам процесс обновления.
"""
from __future__ import annotations

import asyncio
import logging
import os
import shutil
from pathlib import Path
from typing import Tuple

logger = logging.getLogger(__name__)

HOST_PROJECT = Path(os.environ.get("TG_POSTER_HOST_DIR", "/host_project"))
UPDATE_SCRIPT = HOST_PROJECT / "scripts" / "update.sh"
UPDATE_LOG = HOST_PROJECT / "backups" / "last_update.log"
UPDATER_CONTAINER = "tg_poster_updater"
DEFAULT_APP_IMAGE = os.environ.get("TG_POSTER_APP_IMAGE", "tg_poster_docker_app:latest")


def _docker_available() -> bool:
    return Path("/var/run/docker.sock").exists() and shutil.which("docker") is not None


def _read_update_log_tail(max_lines: int = 25) -> str:
    if not UPDATE_LOG.is_file():
        return "Лог обновления пока пуст."
    try:
        lines = UPDATE_LOG.read_text(encoding="utf-8", errors="replace").splitlines()
        tail = lines[-max_lines:] if len(lines) > max_lines else lines
        return "\n".join(tail) or "(пусто)"
    except OSError as exc:
        return f"Не удалось прочитать лог: {exc}"


async def is_update_running() -> bool:
    if not _docker_available():
        return False
    proc = await asyncio.create_subprocess_exec(
        "docker", "ps", "--filter", f"name={UPDATER_CONTAINER}", "--format", "{{.Names}}",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    stdout, _ = await proc.communicate()
    return UPDATER_CONTAINER in (stdout or b"").decode()


async def start_project_update() -> Tuple[bool, str]:
    """Запускает фоновое обновление. Возвращает (успех_старта, сообщение)."""
    if not UPDATE_SCRIPT.is_file():
        return (
            False,
            "Скрипт обновления недоступен.\n"
            "Обновите через SSH:\n"
            "<code>cd /root/tg_poster_docker && bash scripts/update.sh</code>",
        )

    if not _docker_available():
        return (
            False,
            "Docker socket не смонтирован в контейнер app.\n"
            "Обновление из бота недоступно — используйте SSH и "
            "<code>bash scripts/update.sh</code>.",
        )

    if await is_update_running():
        return False, (
            "Обновление уже выполняется.\n\n"
            f"<pre>{_read_update_log_tail()}</pre>"
        )

    UPDATE_LOG.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        "docker", "run", "--rm", "-d",
        "--name", UPDATER_CONTAINER,
        "--entrypoint", "bash",
        "-v", f"{HOST_PROJECT}:/host_project",
        "-v", "/var/run/docker.sock:/var/run/docker.sock",
        "-e", "TG_POSTER_DIR=/host_project",
        "-e", f"TG_POSTER_BRANCH={os.environ.get('TG_POSTER_BRANCH', 'Test_planner')}",
        "-w", "/host_project",
        DEFAULT_APP_IMAGE,
        "-c", f"bash scripts/update.sh > /host_project/backups/last_update.log 2>&1",
    ]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            err = (stderr or stdout or b"").decode(errors="replace").strip()
            logger.error("Updater container failed to start: %s", err)
            return False, f"Не удалось запустить обновление:\n<pre>{err[:500]}</pre>"
    except Exception as exc:
        logger.exception("start_project_update")
        return False, f"Ошибка запуска: {exc}"

    return True, (
        "⏳ <b>Обновление запущено</b>\n\n"
        "Сейчас подтягивается код с GitHub и пересобирается контейнер app.\n"
        "Бот на 1–3 минуты перезапустится — это нормально.\n\n"
        "База данных и токены <b>не затрагиваются</b>.\n"
        "Лог: <code>backups/last_update.log</code>"
    )


async def get_update_status_message() -> str:
    if await is_update_running():
        return (
            "⏳ Обновление выполняется…\n\n"
            f"<pre>{_read_update_log_tail()}</pre>"
        )
    if UPDATE_LOG.is_file():
        return (
            "Последнее обновление (хвост лога):\n\n"
            f"<pre>{_read_update_log_tail()}</pre>"
        )
    return "Обновлений из бота ещё не было."
