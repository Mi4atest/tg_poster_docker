#!/usr/bin/env bash
# Сухой прогон hardening: compose без docker.sock, pull-модель, ADMIN_USER_IDS, systemd.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "[1] docker-compose.yml: без docker.sock, host_project ro, лимиты..."
python3 <<'PY'
from pathlib import Path
text = Path("docker-compose.yml").read_text(encoding="utf-8")
if "docker.sock" in text:
    raise SystemExit("FAIL: docker.sock в docker-compose.yml")
if ":/host_project:ro" not in text:
    raise SystemExit("FAIL: /host_project должен быть :ro")
for needle in ("mem_limit:", "pids_limit:", "cap_drop:", "read_only:", "no-new-privileges"):
    if needle not in text:
        raise SystemExit(f"FAIL: нет {needle!r} в docker-compose.yml")
print("OK: docker-compose hardening")
PY

echo "[2] project_update_service: pull-модель (флаги, без docker run)..."
python3 <<'PY'
from pathlib import Path
text = Path("app/services/project_update_service.py").read_text(encoding="utf-8")
if "/var/run/docker.sock" in text:
    raise SystemExit("FAIL: project_update_service монтирует docker.sock")
if "docker run" in text or "UPDATER_CONTAINER" in text:
    raise SystemExit("FAIL: всё ещё запуск updater-контейнера")
need = ("UPDATE_FLAG", "UPDATE_LOCK", "PRUNE_FLAG", "git ls-remote", "_write_json_flag")
for n in need:
    if n not in text:
        raise SystemExit(f"FAIL: нет {n!r} в project_update_service")
print("OK: pull-модель в коде")
PY

echo "[3] ADMIN_USER_IDS и admin_auth..."
python3 <<'PY'
from pathlib import Path
settings = Path("app/config/settings.py").read_text(encoding="utf-8")
if "ADMIN_USER_IDS" not in settings:
    raise SystemExit("FAIL: нет ADMIN_USER_IDS в settings.py")
auth = Path("app/bot/utils/admin_auth.py").read_text(encoding="utf-8")
if "is_admin_user" not in auth:
    raise SystemExit("FAIL: нет is_admin_user")
handlers = Path("app/bot/handlers/settings.py").read_text(encoding="utf-8")
if "deny_unless_admin_callback" not in handlers:
    raise SystemExit("FAIL: settings.py без admin guard")
print("OK: ADMIN_USER_IDS")
PY

echo "[4] host_tasks.sh + systemd units..."
test -x scripts/host_tasks.sh || chmod +x scripts/host_tasks.sh
test -f scripts/host_tasks.sh
test -f deploy/systemd/tg-poster-host-tasks.service
test -f deploy/systemd/tg-poster-host-tasks.timer
grep -q "update_requested.json" scripts/host_tasks.sh
grep -q "prune_requested.json" scripts/host_tasks.sh
echo "OK: host_tasks + systemd"

echo "[5] Dockerfile runtime без docker-compose..."
python3 <<'PY'
from pathlib import Path
text = Path("Dockerfile").read_text(encoding="utf-8")
runtime = text.split("AS runtime", 1)[1]
if "docker-compose" in runtime:
    raise SystemExit("FAIL: docker-compose в runtime-слое")
if "git" not in runtime:
    raise SystemExit("FAIL: git нужен для проверки версии")
print("OK: Dockerfile runtime")
PY

echo "[6] unit-тесты hardening..."
python3 -m unittest tests.test_admin_auth tests.test_project_update_service -v

echo "[OK] security hardening dry-run passed"
