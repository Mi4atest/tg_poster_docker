#!/usr/bin/env bash
# Локальный сухой прогон того же, что делает .github/workflows/deps-audit.yml
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "[1] Согласованность requirements.txt ↔ requirements.lock + Dockerfile..."
python3 <<'PY'
from pathlib import Path

def parse(path: str):
    out = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "==" not in line:
            continue
        name, ver = line.split("==", 1)
        out[name.lower().replace("_", "-")] = (name, ver)
    return out

req = parse("requirements.txt")
lock = parse("requirements.lock")
errors = []
for key, (name, ver) in req.items():
    if key not in lock:
        errors.append(f"нет в lock: {name}=={ver}")
    elif lock[key][1] != ver:
        errors.append(f"версия {name}: requirements.txt={ver}, lock={lock[key][1]}")
dead = ("python-jose", "passlib", "bcrypt", "ecdsa", "rsa", "pyasn1")
for d in dead:
    if d in lock or d.replace("-", "_") in lock:
        errors.append(f"мёртвый JWT-пакет в lock: {d}")
if "cryptography" not in req:
    errors.append("cryptography должен быть прямой зависимостью (Fernet)")
dockerfile = Path("Dockerfile").read_text(encoding="utf-8")
if "requirements.lock" not in dockerfile:
    errors.append("Dockerfile должен ставить зависимости из requirements.lock")
if errors:
    print("FAIL:")
    print("\n".join(errors))
    raise SystemExit(1)
print(f"OK: {len(req)} прямых pin совпадают с lock ({len(lock)} пакетов)")
PY

echo "[2] pip-audit (Docker python:3.12-slim-bookworm)..."
docker run --rm \
  -v "$ROOT/requirements.lock:/requirements.lock:ro" \
  python:3.12-slim-bookworm \
  bash -c 'pip install -q --no-cache-dir "pip-audit>=2.7,<3" && pip-audit -r /requirements.lock --progress-spinner off'

echo "[OK] deps-audit dry-run passed"
