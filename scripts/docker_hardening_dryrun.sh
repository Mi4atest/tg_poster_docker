#!/usr/bin/env bash
# Сухой прогон этапа 3: multi-stage Dockerfile без gcc/docker-compose в runtime.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

IMAGE="${TG_POSTER_TEST_IMAGE:-tg_poster_docker_app:stage3-dryrun}"

echo "[1] Проверка Dockerfile (multi-stage, lock, без apt docker-compose в runtime)..."
python3 <<'PY'
from pathlib import Path

text = Path("Dockerfile").read_text(encoding="utf-8")
need = ["AS builder", "AS runtime", "requirements.lock", "COPY --from=builder", "python:3.12-slim-bookworm"]
for n in need:
    if n not in text:
        raise SystemExit(f"Dockerfile: нет {n!r}")

runtime = text.split("AS runtime", 1)[1]
for bad in ("gcc", "python3-dev", "libpq-dev"):
    if bad in runtime:
        raise SystemExit(f"runtime-слой Dockerfile содержит {bad!r}")

for good in ("git", "postgresql-client", "fonts-dejavu-core", "libpq5"):
    if good not in runtime:
        raise SystemExit(f"runtime-слой Dockerfile без {good!r}")
if "docker-compose" in runtime:
    raise SystemExit("runtime-слой Dockerfile не должен содержать docker-compose")

print("OK: Dockerfile structure")
PY

echo "[2] docker build (может занять несколько минут)..."
docker build -t "$IMAGE" .

echo "[3] Проверка runtime-образа..."
docker run --rm --entrypoint bash "$IMAGE" -c '
set -e
command -v git >/dev/null
command -v pg_isready >/dev/null
command -v python >/dev/null
pyver=$(python -c "import sys; print(f\"{sys.version_info.major}.{sys.version_info.minor}\")")
test "$pyver" = "3.12" || { echo "FAIL: Python $pyver, expected 3.12"; exit 1; }
echo "python $pyver OK"
! command -v docker-compose >/dev/null 2>&1 || { echo "FAIL: docker-compose в runtime (обновление на хосте)"; exit 1; }
! command -v gcc >/dev/null 2>&1 || { echo "FAIL: gcc в runtime"; exit 1; }
! dpkg -l | grep -q python3-dev || { echo "FAIL: python3-dev в runtime"; exit 1; }
python - <<PY
import aiogram, fastapi, uvicorn, sqlalchemy, aiohttp, alembic, psycopg2, cryptography
from cryptography.fernet import Fernet
print("imports OK")
PY
pip check
freeze_count=$(pip freeze | wc -l)
echo "pip freeze packages: $freeze_count"
'

echo "[4] Сверка lock-пакетов с venv в образе..."
docker run --rm --entrypoint python -v "$ROOT/requirements.lock:/requirements.lock:ro" "$IMAGE" - <<'PY'
from pathlib import Path
import subprocess

def parse(text):
    m = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "==" not in line:
            continue
        n, v = line.split("==", 1)
        m[n.lower().replace("_", "-")] = v
    return m

lock = parse(Path("/requirements.lock").read_text())
freeze = parse(subprocess.check_output(["pip", "freeze"], text=True))
bad = []
for k, v in lock.items():
    if k not in freeze:
        bad.append(f"missing {k}=={v}")
    elif freeze[k] != v:
        bad.append(f"mismatch {k}: lock={v} env={freeze[k]}")
if bad:
    print("\n".join(bad))
    raise SystemExit(1)
print(f"OK: все {len(lock)} pin из lock совпали с venv")
PY

echo "[OK] docker hardening dry-run passed (image=$IMAGE)"
