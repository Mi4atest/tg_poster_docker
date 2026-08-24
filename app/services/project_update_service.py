"""Обновление проекта с GitHub (pull-модель: флаг → host_tasks.sh на хосте).

Бот не монтирует docker.sock: запрос пишется в backups/update_requested.json,
systemd timer на хосте выполняет scripts/host_tasks.sh → scripts/update.sh.
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
BACKUPS_DIR = Path(os.environ.get("TG_POSTER_BACKUPS_DIR", "/app/backups"))
UPDATE_SCRIPT = HOST_PROJECT / "scripts" / "update.sh"
UPDATE_LOG = BACKUPS_DIR / "last_update.log"
UPDATE_META = BACKUPS_DIR / "last_update_meta.json"
UPDATE_FLAG = BACKUPS_DIR / "update_requested.json"
UPDATE_LOCK = BACKUPS_DIR / "update.lock"
PRUNE_FLAG = BACKUPS_DIR / "prune_requested.json"
PRUNE_META = BACKUPS_DIR / "last_prune_meta.json"
DEFAULT_BRANCH = os.environ.get("TG_POSTER_BRANCH", "Test_planner")

_GIT_HTTPS_INSTEADOF = (
    "-c", "url.https://github.com/.insteadOf=git@github.com:",
    "-c", "url.https://github.com/.insteadOf=ssh://git@github.com/",
)

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


async def _git_ls_remote(branch: str) -> Tuple[int, str, str]:
    """Удалённый HEAD без git fetch (не нужна запись в .git)."""
    proc = await asyncio.create_subprocess_exec(
        "git", "-C", str(HOST_PROJECT),
        *_GIT_HTTPS_INSTEADOF,
        "ls-remote", "origin", f"refs/heads/{branch}",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=90.0)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        return 1, "", "timeout"
    out = (stdout or b"").decode(errors="replace").strip()
    err = (stderr or b"").decode(errors="replace").strip()
    if proc.returncode != 0:
        return proc.returncode or 1, "", err
    # "<hash>\trefs/heads/Branch"
    remote_hash = out.split()[0] if out.split() else ""
    return 0, remote_hash, ""


def _ssh_missing_error(err: str) -> bool:
    low = (err or "").lower()
    return "cannot run ssh" in low or "no such file or directory" in low and "ssh" in low


def get_disk_space_info() -> dict:
    try:
        path = HOST_PROJECT if HOST_PROJECT.exists() else BACKUPS_DIR
        usage = shutil.disk_usage(str(path))
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
    raw = (raw or "").strip()
    if not raw:
        return "—"
    try:
        dt = datetime.strptime(raw[:19], "%Y-%m-%d %H:%M:%S")
        return dt.strftime("%d.%m.%Y, %H:%M")
    except ValueError:
        return raw[:16]


def _escape(text: str) -> str:
    return html.escape((text or "").strip() or "—", quote=False)


def _short_commit(full_hash: str) -> str:
    h = (full_hash or "").strip()
    return h[:7] if len(h) >= 7 else h


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


def read_prune_meta() -> Optional[dict]:
    if not PRUNE_META.is_file():
        return None
    try:
        data = json.loads(PRUNE_META.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def is_update_pending() -> bool:
    return UPDATE_FLAG.is_file()


async def check_for_updates(*, use_cache: bool = True) -> UpdateCheck:
    """Сравнение локального HEAD с origin через git ls-remote (без fetch)."""
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

    code, remote_full, err = await _git_ls_remote(branch)
    if code != 0:
        result = UpdateCheck(
            local=local,
            remote_commit="",
            remote_subject="",
            behind=0,
            up_to_date=False,
            fetch_ok=False,
            fetch_error=(err or "git ls-remote не удался")[:200],
        )
        _fetch_cache = {"ts": now, "result": result}
        return result

    if not remote_full:
        result = UpdateCheck(
            local=local,
            remote_commit="",
            remote_subject="",
            behind=0,
            up_to_date=False,
            fetch_ok=False,
            fetch_error=f"нет ветки origin/{branch}",
        )
        _fetch_cache = {"ts": now, "result": result}
        return result

    remote_commit = _short_commit(remote_full)
    remote_ref = f"origin/{branch}"
    _, remote_subject, _ = await _run_git("log", "-1", "--format=%s", remote_ref)
    if not remote_subject:
        _, remote_subject, _ = await _run_git(
            "log", "-1", "--format=%s", remote_full, https_instead_of=True,
        )

    code, local_full, _ = await _run_git("rev-parse", "HEAD")
    behind = 0
    up_to_date = False
    if code == 0 and local_full and remote_full:
        up_to_date = local_full == remote_full
        if not up_to_date:
            code_b, behind_s, _ = await _run_git(
                "rev-list", "--count", f"{local_full}..{remote_full}",
            )
            try:
                behind = int(behind_s) if code_b == 0 else 1
            except ValueError:
                behind = 1
            if behind == 0 and not up_to_date:
                behind = 1

    result = UpdateCheck(
        local=local,
        remote_commit=remote_commit,
        remote_subject=remote_subject,
        behind=behind,
        up_to_date=up_to_date,
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
    running = await is_update_running()
    pending = is_update_pending()
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
    elif pending:
        lines.append("⏳ <b>Обновление в очереди</b> — хост выполнит его в ближайшую минуту.")
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
            lines.append(
                "⚠️ Места критически мало — нажмите «Освободить место» "
                "или дождитесь автоочистки перед сборкой."
            )
        elif disk.get("low"):
            lines.append(
                "⚠️ Мало места (&lt; 3 ГБ) — лучше нажать «Освободить место» "
                "перед обновлением."
            )
    lines.append(
        "<b>Не затрагивается:</b> база, токены, настройки, ссылки, медиа."
    )
    meta = read_update_meta()
    meta_block = _meta_summary_html(meta)
    if meta_block and not running and not pending:
        lines.append("")
        lines.append(meta_block)

    return "\n".join(lines), check, running or pending


def _read_update_log_tail(max_lines: int = 25) -> str:
    if not UPDATE_LOG.is_file():
        return "Лог обновления пока пуст."
    try:
        lines = UPDATE_LOG.read_text(encoding="utf-8", errors="replace").splitlines()
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
    return UPDATE_LOCK.is_file()


def _write_json_flag(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


async def start_project_update(
    *,
    force: bool = False,
    requested_by: Optional[int] = None,
) -> Tuple[bool, str]:
    """Ставит флаг обновления; хост выполняет scripts/host_tasks.sh."""
    if not UPDATE_SCRIPT.is_file():
        return (
            False,
            "Скрипт обновления недоступен.\n"
            "Обновите через SSH:\n"
            "<code>cd /root/tg_poster_docker && bash scripts/update.sh</code>",
        )

    if await is_update_running():
        return False, (
            "Обновление уже выполняется.\n\n"
            f"<pre>{_escape(_read_update_log_tail())}</pre>"
        )

    if is_update_pending():
        return False, (
            "Обновление уже запрошено и ждёт выполнения на хосте (обычно до 1 мин).\n"
            "Откройте этот раздел позже."
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

    invalidate_update_cache()
    _write_json_flag(
        UPDATE_FLAG,
        {
            "force": bool(force),
            "requested_by": requested_by,
            "requested_at": datetime.now().astimezone().isoformat(),
        },
    )
    logger.info("Запрос обновления записан в %s force=%s user=%s", UPDATE_FLAG, force, requested_by)

    return True, (
        "⏳ <b>Запрос на обновление принят</b>\n\n"
        "Хост подтянет код с GitHub и пересоберёт контейнер app "
        "(обычно в течение минуты).\n"
        "Бот на 1–3 минуты перезапустится — это нормально.\n\n"
        "База данных и токены <b>не затрагиваются</b>.\n"
        "После перезапуска откройте этот раздел снова — увидите новую версию."
    )


async def get_update_details_message() -> str:
    parts: list[str] = []
    if await is_update_running():
        parts.append("⏳ Обновление выполняется…")
    elif is_update_pending():
        parts.append("⏳ Обновление в очереди на хосте…")
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


async def free_docker_disk_space(*, requested_by: Optional[int] = None) -> Tuple[bool, str]:
    """Запрос очистки Docker-кэша на хосте (без volumes)."""
    if await is_update_running():
        return False, (
            "Сейчас идёт обновление — дождитесь окончания, "
            "затем повторите «Освободить место»."
        )

    if PRUNE_FLAG.is_file():
        return False, "Очистка уже запрошена — дождитесь выполнения на хосте."

    before = get_disk_space_info()
    free_before = before.get("free_gb", 0)
    _write_json_flag(
        PRUNE_FLAG,
        {
            "requested_by": requested_by,
            "requested_at": datetime.now().astimezone().isoformat(),
        },
    )
    logger.info("Запрос prune записан в %s user=%s", PRUNE_FLAG, requested_by)

    lines = [
        "🧹 <b>Запрос на освобождение места принят</b>",
        "",
        "Хост выполнит <code>docker system prune -af</code> "
        "(старые образы и кэш; volumes / БД / медиа / бэкапы не трогаются).",
        "Обычно это занимает до минуты — обновите экран.",
        "",
        f"Свободно сейчас: <b>{_escape(str(free_before))} ГБ</b>",
    ]
    prune_meta = read_prune_meta()
    if prune_meta and prune_meta.get("finished_at"):
        lines.append("")
        lines.append(f"Последняя очистка: {_escape(str(prune_meta.get('finished_at')))}")
    return True, "\n".join(lines)


async def get_update_status_message() -> str:
    return await get_update_details_message()
