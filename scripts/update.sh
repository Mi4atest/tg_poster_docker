#!/bin/bash
# Безопасное обновление работающего сервера: git pull + пересборка app.
# БД, .env и токены не трогаются (restore не вызывается).
#
# Запуск с хоста:
#   bash scripts/update.sh
# Из бота (через отдельный контейнер-обновлятор):
#   TG_POSTER_DIR=/host_project bash scripts/update.sh
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="${TG_POSTER_DIR:-$(cd "$SCRIPT_DIR/.." && pwd)}"
BRANCH="${TG_POSTER_BRANCH:-Test_planner}"
SKIP_GIT="${TG_POSTER_SKIP_GIT:-0}"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info() { echo -e "${GREEN}[INFO]${NC} $1"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
error() { echo -e "${RED}[ERROR]${NC} $1"; }

cd "$PROJECT_DIR"

if docker compose version >/dev/null 2>&1; then
    DC="docker compose"
elif command -v docker-compose >/dev/null 2>&1; then
    DC="docker-compose"
else
    error "docker compose / docker-compose не найден"
    exit 1
fi

git_pull_with_retry() {
    local attempt
    for attempt in 1 2 3; do
        if git fetch origin; then
            git checkout "$BRANCH" 2>/dev/null || git checkout -f "$BRANCH"
            if git pull origin "$BRANCH" --no-edit; then
                return 0
            fi
        fi
        warn "git fetch/pull — попытка ${attempt}/3 не удалась, пауза 15 с..."
        sleep 15
    done
    return 1
}

if [ "$SKIP_GIT" != "1" ]; then
    info "Обновление кода (ветка ${BRANCH})..."
    if [ -n "$(git status --porcelain 2>/dev/null)" ]; then
        warn "Есть незакоммиченные изменения — сохраняю в stash"
        git stash push -u -m "update_autostash_$(date +%Y%m%d_%H%M%S)" || true
    fi
    if ! git_pull_with_retry; then
        error "Не удалось подтянуть код с GitHub после 3 попыток"
        exit 1
    fi
    info "Коммит: $(git log -1 --oneline)"
else
    info "Пропуск git (TG_POSTER_SKIP_GIT=1)"
fi

if [ -f ".env" ]; then
    info ".env найден — не изменяю"
else
    warn ".env отсутствует — для первичной установки используйте deploy.sh"
fi

info "Сборка образа app..."
$DC build app

info "Пересоздание контейнера app (обход бага docker-compose ContainerConfig)..."
docker rm -f tg_poster_app 2>/dev/null || true
$DC up -d

if [ -f "$PROJECT_DIR/scripts/sync_db_password.sh" ]; then
    bash "$PROJECT_DIR/scripts/sync_db_password.sh" --restart-app \
        || warn "sync_db_password не удался — проверьте пароль БД вручную"
fi

info "Статус контейнеров:"
$DC ps

info "Обновление завершено."
