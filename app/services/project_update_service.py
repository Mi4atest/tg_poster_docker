"""Обновление проекта с GitHub (git pull + пересборка app).

Запускается из бота через отдельный контейнер-обновлятор, чтобы перезапуск app
не обрывал сам процесс обновления.
"""
from __future__ import annotations

import asyncio
import html
import json
import logging
import os
import shutil
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

HOST_PROJECT = Path(os.environ.get("TG_POSTER_HOST_DIR", "/host_project"))
UPDATE_SCRIPT = HOST_PROJECT / "scripts" / "update.sh"
UPDATE_LOG = HOST_PROJECT / "backups" / "last_update.log"
UPDATE_META = HOST_PROJECT / "backups" / "last_update_meta.json"
UPDATER_CONTAINER = "tg_poster_updater"
DEFAULT_APP_IMAGE = os.environ.get("TG_POSTER_APP_IMAGE", "tg_poster_docker_app:latest")
DOCKER_BIN = os.environ.get("TG_POSTER_DOCKER_BIN", "/usr/bin/docker")
DOCKER_COMPOSE_BIN = os.environ.get("TG_POSTER_DOCKER_COMPOSE_BIN", "/usr/bin/docker-compose")
DEFAULT_BRANCH = os.environ.get("TG_POSTER_BRANCH", "Test_planner")

# В контейнере app нет ssh — origin часто git@github.com. Подменяем на HTTPS только для команды.
_GIT_HTTPS_INSTEADOF = (
    "-c", "url.https://github.com/.insteadOf=git@github.com:",
    "-c", "url.https://github.com/.insteadOf=ssh://git@github.com/",
)

# Кэш результата git fetch / сравнения (секунды)
_FETCH_CACHE_TTL = 90
_fetch_cache: dict = {"ts": 0.0, "result": None}


@dataclass
class LocalVersion:
    commit: str
    subject: str
    date_iso: str
    date_display: str
    branch: str
    dirty: bool


@dataclass
class UpdateCheck:
    local: Optional[LocalVersion]
    remote_commit: str
    remote_subject: str
    behind: int
    up_to_date: bool
    fetch_ok: bool
    fetch_error: str = ""


def _docker_binaries() -> tuple[Path, Path]:
    return Path(DOCKER_BIN), Path(DOCKER_COMPOSE_BIN)


def _docker_available() -> bool:
    docker_bin, _ = _docker_binaries()
    return Path("/var/run/docker.sock").exists() and docker_bin.is_file()


def _branch() -> str:
    return os.environ.get("TG_POSTER_BRANCH", DEFAULT_BRANCH).strip() or DEFAULT_BRANCH


async def _run_git(
    *args: str,
    timeout: float = 60.0,
    https_instead_of: bool = False,
) -> Tuple[int, str, str]:
    cmd = ["git", "-C", str(HOST_PROJECT)]
    if https_instead_of:
        cmd.extend(_GIT_HTTPS_INSTEADOF)
    cmd.extend(args)
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        return 1, "", "timeout"
    return (
        proc.returncode or 0,
        (stdout or b"").decode(errors="replace").strip(),
        (stderr or b"").decode(errors="replace").strip(),
    )


def _ssh_missing_error(err: str) -> bool:
    low = (err or "").lower()
    return "cannot run ssh" in low or "no such file or directory" in low and "ssh" in low


def get_disk_space_info() -> dict:
    """Свободное место на разделе с проектом (хост через /host_project)."""
    try:
        usage = shutil.disk_usage(str(HOST_PROJECT if HOST_PROJECT.exists() else "/"))
        free_gb = usage.free / (1024 ** 3)
        total_gb = usage.total / (1024 ** 3)
        used_pct = round(100.0 * (1.0 - usage.free / usage.total), 1) if usage.total else 0.0
        return {
            "free_gb": round(free_gb, 1),
            "total_gb": round(total_gb, 1),
            "used_pct": used_pct,
            "low": free_gb < 3.0,
            "critical": free_gb < 1.5,
        }
    except OSError as exc:
        return {"error": str(exc), "free_gb": 0, "total_gb": 0, "used_pct": 0, "low": True, "critical": True}


def _format_git_date(raw: str) -> str:
    """2026-07-15 22:09:36 +0000 → 15.07.2026, 22:09 UTC."""
    raw = (raw or "").strip()
    if not raw:
        return "—"
    try:
        # git %ci: "2026-07-15 22:09:36 +0000"
        dt = datetime.strptime(raw[:19], "%Y-%m-%d %H:%M:%S")
        return dt.strftime("%d.%m.%Y, %H:%M")
    except ValueError:
        return raw[:16]


def _escape(text: str) -> str:
    return html.escape((text or "").strip() or "—", quote=False)


async def get_local_version() -> Optional[LocalVersion]:
    git_marker = HOST_PROJECT / ".git"
    if not git_marker.exists():
        code, _, _ = await _run_git("rev-parse", "--git-dir")
        if code != 0:
            return None

    code, commit, _ = await _run_git("rev-parse", "--short", "HEAD")
    if code != 0 or not commit:
        return None

    code, subject, _ = await _run_git("log", "-1", "--format=%s")
    code2, date_iso, _ = await _run_git("log", "-1", "--format=%ci")
    code3, branch, _ = await _run_git("rev-parse", "--abbrev-ref", "HEAD")
    code4, dirty_out, _ = await _run_git("status", "--porcelain")

    return LocalVersion(
        commit=commit,
        subject=subject if code == 0 else "",
        date_iso=date_iso if code2 == 0 else "",
        date_display=_format_git_date(date_iso if code2 == 0 else ""),
        branch=branch if code3 == 0 else _branch(),
        dirty=bool(dirty_out) if code4 == 0 else False,
    )


def read_update_meta() -> Optional[dict]:
    if not UPDATE_META.is_file():
        return None
    try:
        data = json.loads(UPDATE_META.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


async def check_for_updates(*, use_cache: bool = True) -> UpdateCheck:
    """git fetch + сравнение локального HEAD с origin/branch."""
    global _fetch_cache
    now = time.monotonic()
    if use_cache and _fetch_cache["result"] is not None:
        if now - float(_fetch_cache["ts"]) < _FETCH_CACHE_TTL:
            return _fetch_cache["result"]

    local = await get_local_version()
    branch = _branch()
    empty = UpdateCheck(
        local=local,
        remote_commit="",
        remote_subject="",
        behind=0,
        up_to_date=False,
        fetch_ok=False,
        fetch_error="нет локального git",
    )
    if local is None:
        return empty

    code, _, err = await _run_git("fetch", "origin", branch, timeout=90.0)
    if code != 0 and _ssh_missing_error(err):
        # В контейнере нет ssh — повторяем через HTTPS insteadOf
        code, _, err = await _run_git(
            "fetch", "origin", branch, timeout=90.0, https_instead_of=True,
        )
    elif code != 0:
        code, _, err = await _run_git("fetch", "origin", timeout=90.0)
        if code != 0 and _ssh_missing_error(err):
            code, _, err = await _run_git(
                "fetch", "origin", timeout=90.0, https_instead_of=True,
            )

    if code != 0:
        result = UpdateCheck(
            local=local,
            remote_commit="",
            remote_subject="",
            behind=0,
            up_to_date=False,
            fetch_ok=False,
            fetch_error=(err or "git fetch не удался")[:200],
        )
        _fetch_cache = {"ts": now, "result": result}
        return result

    remote_ref = f"origin/{branch}"
    code, remote_commit, _ = await _run_git("rev-parse", "--short", remote_ref)
    if code != 0 or not remote_commit:
        result = UpdateCheck(
            local=local,
            remote_commit="",
            remote_subject="",
            behind=0,
            up_to_date=False,
            fetch_ok=False,
            fetch_error=f"нет ветки {remote_ref}",
        )
        _fetch_cache = {"ts": now, "result": result}
        return result

    _, remote_subject, _ = await _run_git("log", "-1", "--format=%s", remote_ref)
    code, behind_s, _ = await _run_git("rev-list", "--count", f"HEAD..{remote_ref}")
    try:
        behind = int(behind_s) if code == 0 else 0
    except ValueError:
        behind = 0

    result = UpdateCheck(
        local=local,
        remote_commit=remote_commit,
        remote_subject=remote_subject,
        behind=behind,
        up_to_date=(behind == 0),
        fetch_ok=True,
    )
    _fetch_cache = {"ts": now, "result": result}
    return result


def invalidate_update_cache() -> None:
    global _fetch_cache
    _fetch_cache = {"ts": 0.0, "result": None}


def _meta_summary_html(meta: Optional[dict]) -> str:
    if not meta:
        return ""
    status = meta.get("status") or ""
    finished = meta.get("finished_at") or ""
    commit = meta.get("commit") or ""
    subject = meta.get("subject") or ""
    labels = {
        "up_to_date": "проверка: уже актуально",
        "updated": "успешно обновлено",
        "failed": "ошибка обновления",
        "skipped_git": "без git (force rebuild)",
    }
    label = labels.get(status, status or "—")
    lines = [f"Последний запуск: <b>{_escape(label)}</b>"]
    if finished:
        # ISO or plain
        display = finished
        try:
            dt = datetime.fromisoformat(finished.replace("Z", "+00:00"))
            display = dt.astimezone().strftime("%d.%m.%Y, %H:%M")
        except ValueError:
            pass
        lines.append(f"Когда: {_escape(display)}")
    if commit:
        lines.append(f"Коммит после: <code>{_escape(commit)}</code>")
    if subject:
        lines.append(f"«{_escape(subject)}»")
    return "\n".join(lines)


async def build_update_screen(*, refresh: bool = True) -> Tuple[str, UpdateCheck, bool]:
    """Текст экрана обновления + результат проверки + running."""
    running = await is_update_running()
    check = await check_for_updates(use_cache=not refresh)

    lines = ["🔄 <b>Обновить с GitHub</b>", ""]

    if check.local:
        loc = check.local
        lines.append("<b>На сервере сейчас:</b>")
        lines.append(f"• Дата: {_escape(loc.date_display)}")
        lines.append(f"• Изменение: {_escape(loc.subject)}")
        lines.append(
            f"• Код: <code>{_escape(loc.commit)}</code>  ·  ветка "
            f"<code>{_escape(loc.branch)}</code>"
        )
        if loc.dirty:
            lines.append("• ⚠️ На сервере есть незакоммиченные правки")
    else:
        lines.append("Не удалось прочитать версию из git (репозиторий недоступен).")

    lines.append("")

    if running:
        lines.append("⏳ <b>Сейчас идёт обновление…</b>")
        lines.append("Бот может на 1–3 минуты перезапуститься — это нормально.")
    elif not check.fetch_ok:
        lines.append(f"⚠️ Не удалось проверить GitHub: {_escape(check.fetch_error)}")
        lines.append("Можно попробовать обновить или зайти позже.")
    elif check.up_to_date:
        lines.append("✅ <b>Уже последняя версия</b> — обновлять не нужно.")
    else:
        n = check.behind
        plural = (
            "изменение" if n == 1 else
            "изменения" if 2 <= n <= 4 else
            "изменений"
        )
        lines.append(f"⬇️ <b>Есть обновление:</b> {n} {plural}")
        if check.remote_subject:
            lines.append(f"На GitHub: «{_escape(check.remote_subject)}»")
        if check.remote_commit:
            lines.append(f"Код на GitHub: <code>{_escape(check.remote_commit)}</code>")

    lines.append("")
    disk = get_disk_space_info()
    if "error" not in disk:
        disk_line = (
            f"💾 Диск: свободно <b>{disk['free_gb']} ГБ</b> "
            f"из {disk['total_gb']} ГБ ({disk['used_pct']}% занято)"
        )
        lines.append(disk_line)
        if disk.get("critical"):
            lines.append("⚠️ Места критически мало — перед сборкой будет очистка Docker-кэша.")
        elif disk.get("low"):
            lines.append("⚠️ Мало места (&lt; 3 ГБ). Перед полной сборкой лучше очистить образы.")
    lines.append(
        "<b>Не затрагивается:</b> база, токены, настройки, ссылки, медиа."
    )
    meta = read_update_meta()
    meta_block = _meta_summary_html(meta)
    if meta_block and not running:
        lines.append("")
        lines.append(meta_block)

    return "\n".join(lines), check, running


async def _resolve_host_bind_path() -> str:
    """Путь на хосте для docker run -v (не путать с /host_project внутри контейнера)."""
    explicit = os.environ.get("TG_POSTER_HOST_BIND", "").strip()
    if explicit and Path(explicit).is_dir():
        return explicit

    mountinfo = Path("/proc/self/mountinfo")
    if mountinfo.is_file():
        for line in mountinfo.read_text(encoding="utf-8", errors="replace").splitlines():
            parts = line.split()
            if len(parts) >= 5 and parts[4] == "/host_project":
                candidate = parts[3]
                if candidate.startswith("/"):
                    return candidate

    container = os.environ.get("TG_POSTER_CONTAINER_NAME", "tg_poster_app")
    docker_bin, _ = _docker_binaries()
    proc = await asyncio.create_subprocess_exec(
        str(docker_bin),
        "inspect",
        container,
        "--format",
        '{{range .Mounts}}{{if eq .Destination "/host_project"}}{{.Source}}{{end}}{{end}}',
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    source = (stdout or b"").decode().strip()
    if source and Path(source).is_dir():
        return source

    err = (stderr or b"").decode(errors="replace").strip()
    logger.warning("Не удалось определить host bind для /host_project: %s", err or "пусто")
    return str(HOST_PROJECT)


def _read_update_log_tail(max_lines: int = 25) -> str:
    if not UPDATE_LOG.is_file():
        return "Лог обновления пока пуст."
    try:
        lines = UPDATE_LOG.read_text(encoding="utf-8", errors="replace").splitlines()
        # Убрать ANSI-цвета для читаемости в Telegram
        cleaned = []
        for line in lines:
            s = line
            for code in ("\033[0;31m", "\033[0;32m", "\033[1;33m", "\033[0m", "\033[0;33m"):
                s = s.replace(code, "")
            cleaned.append(s)
        tail = cleaned[-max_lines:] if len(cleaned) > max_lines else cleaned
        return "\n".join(tail) or "(пусто)"
    except OSError as exc:
        return f"Не удалось прочитать лог: {exc}"


async def is_update_running() -> bool:
    if not _docker_available():
        return False
    proc = await asyncio.create_subprocess_exec(
        str(_docker_binaries()[0]), "ps", "--filter", f"name={UPDATER_CONTAINER}", "--format", "{{.Names}}",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    stdout, _ = await proc.communicate()
    return UPDATER_CONTAINER in (stdout or b"").decode()


async def start_project_update(*, force: bool = False) -> Tuple[bool, str]:
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
            f"<pre>{_escape(_read_update_log_tail())}</pre>"
        )

    if not force:
        check = await check_for_updates(use_cache=False)
        if check.fetch_ok and check.up_to_date:
            screen, _, _ = await build_update_screen(refresh=False)
            return False, (
                screen + "\n\n"
                "Обновление <b>не запущено</b> — на сервере уже последняя версия.\n"
                "Если нужна принудительная пересборка — нажмите «Обновить всё равно»."
            )

    docker_bin, compose_bin = _docker_binaries()
    host_bind = await _resolve_host_bind_path()
    logger.info(
        "Запуск обновления: host_bind=%s image=%s force=%s",
        host_bind, DEFAULT_APP_IMAGE, force,
    )

    UPDATE_LOG.parent.mkdir(parents=True, exist_ok=True)
    invalidate_update_cache()

    force_env = "1" if force else "0"
    cmd = [
        str(docker_bin), "run", "--rm", "-d",
        "--name", UPDATER_CONTAINER,
        "--entrypoint", "bash",
        # Монтируем по тому же абсолютному пути, что на хосте — иначе docker-compose v1
        # внутри updater подставляет /host_project/... с хоста (пустая папка).
        "-v", f"{host_bind}:{host_bind}",
        "-v", "/var/run/docker.sock:/var/run/docker.sock",
        "-v", f"{docker_bin}:{docker_bin}:ro",
        "-v", f"{compose_bin}:{compose_bin}:ro",
        "-e", f"TG_POSTER_DIR={host_bind}",
        "-e", "COMPOSE_PROJECT_NAME=tg_poster_docker",
        "-e", f"TG_POSTER_BRANCH={_branch()}",
        "-e", f"TG_POSTER_FORCE_UPDATE={force_env}",
        "-w", host_bind,
        DEFAULT_APP_IMAGE,
        "-c", f"bash scripts/update.sh > {host_bind}/backups/last_update.log 2>&1",
    ]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        out = (stdout or b"").decode(errors="replace").strip()
        err = (stderr or b"").decode(errors="replace").strip()
        if proc.returncode != 0:
            logger.error("Updater container failed to start: %s", err or out)
            return False, f"Не удалось запустить обновление:\n<pre>{_escape((err or out)[:500])}</pre>"
    except Exception as exc:
        logger.exception("start_project_update")
        return False, f"Ошибка запуска: {_escape(str(exc))}"

    return True, (
        "⏳ <b>Обновление запущено</b>\n\n"
        "Сейчас подтягивается код с GitHub и пересобирается контейнер app.\n"
        "Бот на 1–3 минуты перезапустится — это нормально.\n\n"
        "База данных и токены <b>не затрагиваются</b>.\n"
        "После перезапуска откройте этот раздел снова — увидите новую версию."
    )


async def get_update_details_message() -> str:
    """Подробности: meta + хвост лога (вторичный экран)."""
    parts: list[str] = []
    if await is_update_running():
        parts.append("⏳ Обновление выполняется…")
    meta = read_update_meta()
    meta_block = _meta_summary_html(meta)
    if meta_block:
        parts.append(meta_block)
    else:
        parts.append("Метаданных последнего запуска пока нет.")
    parts.append("")
    parts.append("<b>Хвост лога:</b>")
    parts.append(f"<pre>{_escape(_read_update_log_tail())}</pre>")
    return "\n".join(parts)


# Обратная совместимость имени
async def get_update_status_message() -> str:
    return await get_update_details_message()
