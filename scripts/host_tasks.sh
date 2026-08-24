#!/bin/bash
# Задачи на хосте по флагам из бота (pull-модель, без docker.sock в app).
# Вызывается systemd timer'ом; не запускайте параллельно вручную во время update.
#
# Флаги (в backups/):
#   update_requested.json — git pull + rebuild app
#   prune_requested.json  — docker system prune -af
#   update.lock           — update.sh выполняется
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="${TG_POSTER_DIR:-$(cd "$SCRIPT_DIR/.." && pwd)}"
BACKUPS_DIR="${PROJECT_DIR}/backups"
UPDATE_FLAG="${BACKUPS_DIR}/update_requested.json"
PRUNE_FLAG="${BACKUPS_DIR}/prune_requested.json"
UPDATE_LOCK="${BACKUPS_DIR}/update.lock"
PRUNE_META="${BACKUPS_DIR}/last_prune_meta.json"

mkdir -p "$BACKUPS_DIR"

_read_json_field() {
    local file="$1" field="$2"
    python3 - "$file" "$field" <<'PY'
import json, sys
path, key = sys.argv[1], sys.argv[2]
try:
    data = json.load(open(path, encoding="utf-8"))
    val = data.get(key)
    print("" if val is None else val)
except Exception:
    print("")
PY
}

write_prune_meta() {
    local status="$1" ok="${2:-true}"
    local finished
    finished="$(date -Iseconds 2>/dev/null || date '+%Y-%m-%dT%H:%M:%S%z')"
    cat > "$PRUNE_META" <<EOF
{
  "ok": ${ok},
  "status": "${status}",
  "finished_at": "${finished}"
}
EOF
}

run_prune() {
    if [ ! -f "$PRUNE_FLAG" ]; then
        return 0
    fi
    if [ -f "$UPDATE_LOCK" ]; then
        return 0
    fi
    local flag_tmp="${PRUNE_FLAG}.processing"
    mv "$PRUNE_FLAG" "$flag_tmp"
    local free_before free_after
    free_before="$(df -Pk "$PROJECT_DIR" | awk 'NR==2 {print $4}')"
    if docker system prune -af; then
        free_after="$(df -Pk "$PROJECT_DIR" | awk 'NR==2 {print $4}')"
        write_prune_meta "done" true
        echo "[host_tasks] prune ok, free_kb ${free_before} -> ${free_after}"
    else
        write_prune_meta "failed" false
        echo "[host_tasks] prune failed" >&2
    fi
    rm -f "$flag_tmp"
}

run_update() {
    if [ ! -f "$UPDATE_FLAG" ]; then
        return 0
    fi
    if [ -f "$UPDATE_LOCK" ]; then
        return 0
    fi
    local force requested_by flag_tmp="${UPDATE_FLAG}.processing"
    force="$(_read_json_field "$UPDATE_FLAG" force)"
    requested_by="$(_read_json_field "$UPDATE_FLAG" requested_by)"
    mv "$UPDATE_FLAG" "$flag_tmp"
    touch "$UPDATE_LOCK"
    trap 'rm -f "$UPDATE_LOCK"' EXIT

    echo "[host_tasks] update start force=${force} requested_by=${requested_by}"
    export TG_POSTER_DIR="$PROJECT_DIR"
    export TG_POSTER_FROM_HOST_TASKS=1
    if [ "$force" = "1" ] || [ "$force" = "true" ] || [ "$force" = "True" ]; then
        export TG_POSTER_FORCE_UPDATE=1
    else
        export TG_POSTER_FORCE_UPDATE=0
    fi
    local update_log="${BACKUPS_DIR}/last_update.log"
    : > "$update_log"
    bash "$PROJECT_DIR/scripts/update.sh" >> "$update_log" 2>&1
    rm -f "$flag_tmp" "$UPDATE_LOCK"
    trap - EXIT
    # #region agent log
    python3 -c "import json,time,os; p='${BACKUPS_DIR}/last_update.log'; open('/root/tg_poster_docker/.cursor/debug-a7d656.log','a').write(json.dumps({'sessionId':'a7d656','hypothesisId':'C','location':'host_tasks.sh:run_update','message':'update finished','data':{'log_size':os.path.getsize(p) if os.path.isfile(p) else 0},'timestamp':int(time.time()*1000)})+'\n')" 2>/dev/null || true
    # #endregion
    echo "[host_tasks] update finished"
}

run_prune
run_update
