#!/bin/bash
# =============================================================================
#  Синхронизация локального состояния сервера → GitHub
#
#  Сценарий: основной код «живёт» на этом сервере, в GitHub — старая версия.
#  Этот скрипт публикует текущее состояние в репозиторий, чтобы потом на других
#  серверах обновляться одной командой (deploy.sh).
#
#  Использование:
#    bash scripts/sync_to_github.sh "текст коммита"
#
#  .env и прочие секреты НЕ попадают в коммит (см. .gitignore).
# =============================================================================
set -e

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info() { echo -e "${GREEN}[INFO]${NC} $1"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
error(){ echo -e "${RED}[ERROR]${NC} $1"; }

# Переходим в корень проекта (скрипт лежит в scripts/)
cd "$(dirname "$0")/.."

REPO_URL="${TG_POSTER_REPO:-https://github.com/Mi4atest/tg_poster_docker.git}"
BRANCH="${TG_POSTER_BRANCH:-Test_planner}"
COMMIT_MSG="${1:-sync from server $(date +%Y-%m-%d\ %H:%M:%S)}"

# Защита: .env не должен попасть в репозиторий
if git check-ignore -q .env 2>/dev/null || ! [ -f .env ]; then
    :
else
    if [ ! -f .gitignore ] || ! grep -qx '.env' .gitignore; then
        error ".env не игнорируется! Проверьте .gitignore перед публикацией."
        exit 1
    fi
fi

if [ ! -d ".git" ]; then
    info "Инициализация git-репозитория..."
    git init
    git branch -M "$BRANCH"
    git remote add origin "$REPO_URL" 2>/dev/null || git remote set-url origin "$REPO_URL"
fi

# Убедимся, что remote задан
git remote get-url origin >/dev/null 2>&1 || git remote add origin "$REPO_URL"

# Идентичность для коммитов (git требует имя/почту)
if ! git config user.email >/dev/null 2>&1; then
    warn "Не настроена идентичность git. Задайте один раз, например:"
    warn "    git config user.name \"Your Name\""
    warn "    git config user.email \"you@example.com\""
    error "Прервано: настройте user.name/user.email и запустите снова."
    exit 1
fi

info "Файлы для коммита:"
git add -A
git status --short

if [ -z "$(git status --porcelain)" ]; then
    info "Нет изменений для коммита."
else
    git commit -m "$COMMIT_MSG"
fi

info "Отправка в origin/$BRANCH..."
if git push -u origin "$BRANCH"; then
    info "Готово. На других серверах обновляйтесь командой deploy.sh"
else
    warn "Push отклонён. Если в GitHub старая расходящаяся история и вы хотите"
    warn "перезаписать её состоянием сервера, выполните вручную (ОСТОРОЖНО):"
    warn "    git push -u origin $BRANCH --force-with-lease"
fi
