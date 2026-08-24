#!/bin/bash
# Безопасное обновление работающего сервера: git pull + пересборка app.
# БД, .env и токены не трогаются (restore не вызывается).
#
# Запуск с хоста:
#   bash scripts/update.sh
# Из бота (через отдельный контейнер-обновлятор):
#   TG_POSTER_DIR=/host_project bash scripts/update.sh
#
# Переменные:
#   TG_POSTER_FORCE_UPDATE=1 — пересобрать даже если уже актуальная версия
#   TG_POSTER_SKIP_GIT=1       — пропустить git (только rebuild)
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="${TG_POSTER_DIR:-$(cd "$SCRIPT_DIR/.." && pwd)}"
BRANCH="${TG_POSTER_BRANCH:-Test_planner}"
SKIP_GIT="${TG_POSTER_SKIP_GIT:-0}"
FORCE_UPDATE="${TG_POSTER_FORCE_UPDATE:-0}"
COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-tg_poster_docker}"
COMPOSE_FILE="${PROJECT_DIR}/docker-compose.yml"
META_FILE="${PROJECT_DIR}/backups/last_update_meta.json"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info() { echo -e "${GREEN}[INFO]${NC} $1"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
error() { echo -e "${RED}[ERROR]${NC} $1"; }

write_meta() {
    local status="$1"
    local ok="${2:-true}"
    mkdir -p "$(dirname "$META_FILE")"
    local commit subject finished
    commit="$(git -C "$PROJECT_DIR" log -1 --format='%h' 2>/dev/null || echo "")"
    subject="$(git -C "$PROJECT_DIR" log -1 --format='%s' 2>/dev/null || echo "")"
    finished="$(date -Iseconds 2>/dev/null || date '+%Y-%m-%dT%H:%M:%S%z')"
    # Экранируем кавычки в subject для JSON
    subject_json=$(printf '%s' "$subject" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))' 2>/dev/null \
        || printf '"%s"' "$(printf '%s' "$subject" | sed 's/\\/\\\\/g; s/"/\\"/g')")
    cat > "$META_FILE" <<EOF
{
  "ok": ${ok},
  "status": "${status}",
  "commit": "${commit}",
  "subject": ${subject_json},
  "branch": "${BRANCH}",
  "finished_at": "${finished}",
  "force": $([ "$FORCE_UPDATE" = "1" ] && echo true || echo false)
}
EOF
}

_on_err() {
    write_meta "failed" false || true
}
trap _on_err ERR

cd "$PROJECT_DIR"

if [ -f ".env" ]; then
    set -a
    # shellcheck disable=SC1091
    source .env
    set +a
fi

if docker compose version >/dev/null 2>&1; then
    DC=(docker compose -p "${COMPOSE_PROJECT_NAME}" -f "${COMPOSE_FILE}")
elif docker-compose version >/dev/null 2>&1; then
    DC=(docker-compose -p "${COMPOSE_PROJECT_NAME}" -f "${COMPOSE_FILE}")
else
    error "docker compose / docker-compose не найден или не запускается"
    write_meta "failed" false
    exit 1
fi

git_cmd() {
    # В updater-контейнере часто нет ssh, а origin = git@github.com
    git -c "url.https://github.com/.insteadOf=git@github.com:" \
        -c "url.https://github.com/.insteadOf=ssh://git@github.com/" \
        "$@"
}

git_fetch_with_retry() {
    local attempt
    for attempt in 1 2 3; do
        if git_cmd fetch origin; then
            return 0
        fi
        warn "git fetch — попытка ${attempt}/3 не удалась, пауза 15 с..."
        sleep 15
    done
    return 1
}

git_pull_with_retry() {
    local attempt
    for attempt in 1 2 3; do
        if git_cmd fetch origin; then
            git checkout "$BRANCH" 2>/dev/null || git checkout -f "$BRANCH"
            if git_cmd pull origin "$BRANCH" --no-edit; then
                return 0
            fi
        fi
        warn "git fetch/pull — попытка ${attempt}/3 не удалась, пауза 15 с..."
        sleep 15
    done
    return 1
}

ensure_disk_space() {
    # На VPS ~20ГБ перед build чистим неиспользуемые образы, если свободно < 3ГБ
    # НЕ трогаем volumes (postgres_data).
    local avail_kb threshold_kb
    avail_kb=$(df -Pk "$PROJECT_DIR" | awk 'NR==2 {print $4}')
    threshold_kb=$((3 * 1024 * 1024))  # 3 GiB
    info "Свободно на диске: $(df -h "$PROJECT_DIR" | awk 'NR==2 {print $4}') (раздел $(df -h "$PROJECT_DIR" | awk 'NR==2 {print $2}'))"
    if [ -n "$avail_kb" ] && [ "$avail_kb" -lt "$threshold_kb" ]; then
        warn "Мало места (<3 ГБ) — docker system prune -af (без volumes)…"
        docker system prune -af || warn "prune не удался — продолжаю сборку"
        info "После очистки: $(df -h "$PROJECT_DIR" | awk 'NR==2 {print $4}') свободно"
    fi
}

if [ "$SKIP_GIT" != "1" ]; then
    info "Проверка обновлений (ветка ${BRANCH})..."
    if [ -n "$(git status --porcelain 2>/dev/null)" ]; then
        warn "Есть локальные изменения — stash не делаем, чтобы не потерять правки деплоя"
    fi
    if ! git_fetch_with_retry; then
        error "Не удалось связаться с GitHub после 3 попыток"
        write_meta "failed" false
        exit 1
    fi
    git checkout "$BRANCH" 2>/dev/null || git checkout -f "$BRANCH"

    LOCAL="$(git rev-parse HEAD)"
    REMOTE="$(git rev-parse "origin/${BRANCH}" 2>/dev/null || true)"
    if [ -z "$REMOTE" ]; then
        error "Нет origin/${BRANCH} после fetch"
        write_meta "failed" false
        exit 1
    fi

    if [ "$LOCAL" = "$REMOTE" ] && [ "$FORCE_UPDATE" != "1" ]; then
        info "Уже актуальная версия: $(git log -1 --oneline)"
        info "Пересборка не нужна. Для принудительной: TG_POSTER_FORCE_UPDATE=1"
        write_meta "up_to_date" true
        info "Обновление завершено (без изменений)."
        exit 0
    fi

    if [ "$LOCAL" = "$REMOTE" ] && [ "$FORCE_UPDATE" = "1" ]; then
        info "Версия актуальна, но FORCE — пересобираем образ"
    else
        info "Подтягиваю код…"
        if ! git_pull_with_retry; then
            error "Не удалось подтянуть код с GitHub после 3 попыток"
            write_meta "failed" false
            exit 1
        fi
        info "Коммит: $(git log -1 --oneline)"
        # После pull на диске новый update.sh, а этот процесс всё ещё выполняет
        # старый inode (без свежего fallback) — перезапускаем себя.
        info "Перезапуск update.sh с диска (после git pull)…"
        exec env TG_POSTER_SKIP_GIT=1 \
            TG_POSTER_DIR="$PROJECT_DIR" \
            TG_POSTER_BRANCH="$BRANCH" \
            TG_POSTER_FORCE_UPDATE="${FORCE_UPDATE}" \
            COMPOSE_PROJECT_NAME="$COMPOSE_PROJECT_NAME" \
            bash "$PROJECT_DIR/scripts/update.sh"
    fi
else
    info "Пропуск git (TG_POSTER_SKIP_GIT=1)"
fi

if [ -f ".env" ]; then
    info ".env найден — не изменяю"
else
    warn ".env отсутствует — для первичной установки используйте deploy.sh"
fi

ensure_disk_space

info "Сборка образа app..."
"${DC[@]}" build app

info "Пересоздание только контейнера app (db/nginx не трогаем)..."
docker rm -f tg_poster_app 2>/dev/null || true
docker start tg_poster_db 2>/dev/null || "${DC[@]}" up -d --no-recreate db

# docker-compose падает, если опции сети изменились (например MTU), а db/nginx ещё на старой сети.
# Тогда app уже удалён — без fallback бот остаётся мёртвым.
set +e
UP_OUT="$("${DC[@]}" up -d --no-deps app 2>&1)"
UP_RC=$?
set -e
printf '%s\n' "$UP_OUT"

if [ "$UP_RC" -ne 0 ]; then
    # compose v1: Network "…" needs to be recreated - option "…mtu" has changed
    # compose v2/v5: tries to remove network → "has active endpoints" (db/nginx still attached)
    if echo "$UP_OUT" | grep -qiE 'needs to be recreated|option .* has changed|active endpoints|removing network'; then
        warn "Сеть Docker изменилась (MTU/опции) — пересоздаём сеть и сервисы (volume БД сохраняется)..."
        # down без -v: контейнеры+сеть уходят, postgres_data и bind-mounts (.env, media) остаются
        set +e
        DOWN_OUT="$("${DC[@]}" down --remove-orphans 2>&1)"
        DOWN_RC=$?
        set -e
        printf '%s\n' "$DOWN_OUT"
        if [ "$DOWN_RC" -ne 0 ]; then
            error "Не удалось пересоздать стек (down failed)"
            write_meta "failed" false
            exit 1
        fi
        set +e
        UPALL_OUT="$("${DC[@]}" up -d 2>&1)"
        UPALL_RC=$?
        set -e
        printf '%s\n' "$UPALL_OUT"
        if [ "$UPALL_RC" -ne 0 ]; then
            error "Не удалось поднять стек после пересоздания сети"
            write_meta "failed" false
            exit 1
        fi
    else
        error "Не удалось поднять контейнер app"
        write_meta "failed" false
        exit 1
    fi
fi

if [ -f "$PROJECT_DIR/scripts/sync_db_password.sh" ]; then
    bash "$PROJECT_DIR/scripts/sync_db_password.sh" --restart-app \
        || warn "sync_db_password не удался — проверьте пароль БД вручную"
fi

info "Статус контейнеров:"
"${DC[@]}" ps

if [ "$SKIP_GIT" = "1" ]; then
    write_meta "skipped_git" true
else
    write_meta "updated" true
fi

info "Обновление завершено."
