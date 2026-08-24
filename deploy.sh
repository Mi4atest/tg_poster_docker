#!/bin/bash
# =============================================================================
#  tg_poster — единый скрипт развёртывания и обновления
#
#  Установка/обновление одной командой (в т.ч. на чистой системе без Docker):
#    bash <(wget -qO- --no-hsts --inet4-only \
#      https://raw.githubusercontent.com/Mi4atest/tg_poster_docker/Test_planner/deploy.sh)
#
#  Бэкап БД до деплоя: положите в /root файл *_backup_*.sql.gz
#  или задайте TG_POSTER_BACKUP=/root/your_backup.sql.gz
# =============================================================================
set -e

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
info()     { echo -e "${GREEN}[INFO]${NC} $1"; }
warn()     { echo -e "${YELLOW}[WARN]${NC} $1"; }
error()    { echo -e "${RED}[ERROR]${NC} $1"; }
question() { echo -e "${BLUE}[?]${NC} $1"; }

REPO_URL="${TG_POSTER_REPO:-https://github.com/Mi4atest/tg_poster_docker.git}"
BRANCH="${TG_POSTER_BRANCH:-Test_planner}"
PROJECT_DIR="${TG_POSTER_DIR:-/root/tg_poster_docker}"
BACKUP_ROOT="${TG_POSTER_BACKUP_ROOT:-/root}"

# --- Поиск бэкапа (до деплоя: /root; после: /root + project/backups) ---
find_latest_backup() {
    local search_dirs=()
    [ -n "$BACKUP_ROOT" ] && [ -d "$BACKUP_ROOT" ] && search_dirs+=("$BACKUP_ROOT")
    [ -d "$PROJECT_DIR/backups" ] && search_dirs+=("$PROJECT_DIR/backups")

    local best=""
    local dir f
    for dir in "${search_dirs[@]}"; do
        while IFS= read -r -d '' f; do
            if [ -z "$best" ] || [ "$f" -nt "$best" ]; then
                best="$f"
            fi
        done < <(find "$dir" -maxdepth 1 -type f -name '*_backup_*.gz' -print0 2>/dev/null)
    done
    echo "$best"
}

resolve_staged_backup() {
    if [ -n "${TG_POSTER_BACKUP:-}" ] && [ -f "$TG_POSTER_BACKUP" ]; then
        echo "$TG_POSTER_BACKUP"
        return
    fi
    find_latest_backup
}

# --- Установка зависимостей на чистой системе (Debian/Ubuntu) ---
install_base_packages() {
    if ! command -v apt-get >/dev/null 2>&1; then
        error "Автоустановка поддерживается только для Debian/Ubuntu (apt). Установите git, docker и docker compose вручную."
        exit 1
    fi
    info "Установка базовых пакетов (git, wget, curl, ca-certificates)..."
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq
    apt-get install -y -qq git wget curl ca-certificates gnupg lsb-release openssl
}

install_docker_if_missing() {
    if command -v docker >/dev/null 2>&1; then
        if docker compose version >/dev/null 2>&1 || command -v docker-compose >/dev/null 2>&1; then
            return 0
        fi
    fi

    install_base_packages
    info "Установка Docker и Docker Compose..."

    if [ ! -f /etc/apt/sources.list.d/docker.list ]; then
        install -m 0755 -d /etc/apt/keyrings
        if [ ! -f /etc/apt/keyrings/docker.asc ]; then
            curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc 2>/dev/null \
                || curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc
            chmod a+r /etc/apt/keyrings/docker.asc
        fi
        local distro codename
        if [ -f /etc/os-release ]; then
            # shellcheck disable=SC1091
            . /etc/os-release
            distro="${ID:-ubuntu}"
            codename="${VERSION_CODENAME:-}"
        fi
        if [ -z "$codename" ]; then
            codename="$(lsb_release -cs 2>/dev/null || echo bookworm)"
        fi
        echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
https://download.docker.com/linux/${distro} ${codename} stable" \
            > /etc/apt/sources.list.d/docker.list
    fi

    apt-get update -qq
    if apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin 2>/dev/null; then
        :
    else
        warn "Официальный репозиторий Docker недоступен — пробую docker.io из дистрибутива..."
        apt-get install -y -qq docker.io docker-compose-plugin 2>/dev/null \
            || apt-get install -y -qq docker.io docker-compose
    fi

    systemctl enable --now docker 2>/dev/null || service docker start 2>/dev/null || true
    info "Docker установлен: $(docker --version 2>/dev/null || echo '?')"
}

ensure_docker_compose() {
    if docker compose version >/dev/null 2>&1; then
        DC="docker compose"
    elif command -v docker-compose >/dev/null 2>&1; then
        DC="docker-compose"
    else
        install_docker_if_missing
        if docker compose version >/dev/null 2>&1; then
            DC="docker compose"
        elif command -v docker-compose >/dev/null 2>&1; then
            DC="docker-compose"
        else
            error "docker compose не найден после установки"
            exit 1
        fi
    fi
    info "Используется: $DC"
}

# --- 1. Префлайт ------------------------------------------------------------
info "=========================================="
info " Развёртывание tg_poster (ветка: $BRANCH)"
info "=========================================="

if ! command -v git >/dev/null 2>&1; then
    install_base_packages
fi
command -v git >/dev/null 2>&1 || { error "git не установлен"; exit 1; }

if ! command -v docker >/dev/null 2>&1; then
    info "Docker не найден — устанавливаю автоматически..."
    install_docker_if_missing
fi
ensure_docker_compose

# Бэкап в /root до клонирования (не создавайте вручную весь tg_poster_docker)
STAGED_BACKUP="$(resolve_staged_backup)"
if [ -n "$STAGED_BACKUP" ]; then
    info "Найден бэкап для восстановления после деплоя: $(basename "$STAGED_BACKUP")"
    info "  (положите *_backup_*.sql.gz в ${BACKUP_ROOT}/ или задайте TG_POSTER_BACKUP=...)"
else
    info "Бэкап в ${BACKUP_ROOT} не найден (опционально: *_backup_*.sql.gz)"
fi

# --- 2. Клонирование / обновление кода --------------------------------------
if [ -d "$PROJECT_DIR/.git" ]; then
    info "Обновление существующего репозитория..."
    cd "$PROJECT_DIR"
    if [ -n "$(git status --porcelain)" ]; then
        warn "Есть незакоммиченные изменения — сохраняю в stash"
        git stash push -u -m "deploy_autostash_$(date +%Y%m%d_%H%M%S)" || true
    fi
    git fetch origin
    git checkout "$BRANCH" 2>/dev/null || git checkout -f "$BRANCH"
    git config pull.rebase false
    git pull origin "$BRANCH" --no-edit || {
        warn "Слияние не удалось — принудительное обновление до origin/$BRANCH"
        git reset --hard "origin/$BRANCH"
    }
elif [ -f "$PROJECT_DIR/docker-compose.yml" ]; then
    warn "Каталог $PROJECT_DIR существует, но без git — обновление кода из GitHub пропущено."
    cd "$PROJECT_DIR"
else
  if [ -d "$PROJECT_DIR" ] && [ -n "$(ls -A "$PROJECT_DIR" 2>/dev/null)" ]; then
    warn "Каталог $PROJECT_DIR занят (часто это только backups/) — очищаю для clone..."
    find "$PROJECT_DIR" -maxdepth 2 -type f -name '*_backup_*.gz' -exec mv -n {} "$BACKUP_ROOT/" \; 2>/dev/null || true
    rm -rf "$PROJECT_DIR"
    STAGED_BACKUP="$(resolve_staged_backup)"
  fi
    info "Клонирование репозитория в $PROJECT_DIR..."
    git clone -b "$BRANCH" "$REPO_URL" "$PROJECT_DIR"
    cd "$PROJECT_DIR"
fi

# --- 3. Мастер .env (только при первом запуске) -----------------------------
if [ ! -f ".env" ]; then
    info ""
    info "Файл .env не найден — первичная настройка."
    info "Нужен минимум данных, остальное настроите в боте: «⚙️ Настройки»."
    echo ""

    read -rp "$(echo -e "${BLUE}[?]${NC} ") Токен основного Telegram-бота (@BotFather): " BOT_TOKEN
    while [ -z "$BOT_TOKEN" ]; do
        read -rp "    Токен обязателен. Введите TELEGRAM_BOT_TOKEN: " BOT_TOKEN
    done

    read -rp "$(echo -e "${BLUE}[?]${NC} ") ID пользователей с доступом к боту через запятую (ALLOWED_USER_IDS): " ALLOWED_IDS
    while [ -z "$ALLOWED_IDS" ]; do
        read -rp "    Нужен хотя бы один ID. Введите ALLOWED_USER_IDS: " ALLOWED_IDS
    done

    read -rp "$(echo -e "${BLUE}[?]${NC} ") ID администраторов (обновление, секреты) [Enter = те же]: " ADMIN_USER_IDS_INPUT
    ADMIN_USER_IDS_INPUT="${ADMIN_USER_IDS_INPUT:-$ALLOWED_IDS}"

    if command -v openssl >/dev/null 2>&1; then
        MASTER_KEY="$(openssl rand -hex 32)"
        DB_PASSWORD="$(openssl rand -hex 16)"
    else
        MASTER_KEY="$(head -c32 /dev/urandom | od -An -tx1 | tr -d ' \n')"
        DB_PASSWORD="$(head -c16 /dev/urandom | od -An -tx1 | tr -d ' \n')"
    fi
    DATABASE_URL="postgresql://postgres:${DB_PASSWORD}@db:5432/tg_poster"

    cat > .env <<EOF
# Сгенерировано deploy.sh $(date +%Y-%m-%d\ %H:%M:%S)
TELEGRAM_BOT_TOKEN=${BOT_TOKEN}
ALLOWED_USER_IDS=${ALLOWED_IDS}
ADMIN_USER_IDS=${ADMIN_USER_IDS_INPUT}
MASTER_KEY=${MASTER_KEY}

DB_USER=postgres
DB_PASSWORD=${DB_PASSWORD}
DB_NAME=tg_poster
DATABASE_URL=${DATABASE_URL}

VK_APP_ID=
VK_APP_SECRET=
VK_ACCESS_TOKEN=
VK_MARKET_ACCESS_TOKEN=
VK_GROUP_ID=

INSTAGRAM_GRAPH_USER_ID=
INSTAGRAM_GRAPH_ACCESS_TOKEN=
INSTAGRAM_GRAPH_APP_ID=
INSTAGRAM_GRAPH_APP_SECRET=
INSTAGRAM_USERNAME=
INSTAGRAM_PASSWORD=

MAX_BOT_TOKEN=
EOF
    info ".env создан. Каналы/бэкап/отчёты/подписи настроите в боте после запуска."
else
    info ".env найден — оставляю без изменений."
    if ! grep -qE '^MASTER_KEY=.+' .env; then
        if command -v openssl >/dev/null 2>&1; then
            NEW_KEY="$(openssl rand -hex 32)"
        else
            NEW_KEY="$(head -c32 /dev/urandom | od -An -tx1 | tr -d ' \n')"
        fi
        sed -i '/^MASTER_KEY=$/d' .env
        echo "MASTER_KEY=${NEW_KEY}" >> .env
        warn "В .env добавлен сгенерированный MASTER_KEY."
    fi
fi

# --- 4. Сборка и запуск -----------------------------------------------------
info ""
if [ -f "scripts/update.sh" ]; then
    TG_POSTER_SKIP_GIT=1 bash scripts/update.sh
else
    info "Сборка образов..."
    $DC build
    info "Запуск контейнеров (миграции применятся автоматически в entrypoint)..."
    $DC up -d
    if [ -f "scripts/sync_db_password.sh" ]; then
        bash scripts/sync_db_password.sh --restart-app || warn "Синхронизация пароля БД не удалась — проверьте логи db/app"
    fi
    info "Статус контейнеров:"
    $DC ps
fi

# --- 5. Восстановление из бэкапа (опционально, только первичная установка) ---
mkdir -p backups

LATEST_BACKUP=""
if [ -n "$STAGED_BACKUP" ] && [ -f "$STAGED_BACKUP" ]; then
    LATEST_BACKUP="backups/$(basename "$STAGED_BACKUP")"
    if [ ! -f "$LATEST_BACKUP" ] || [ "$STAGED_BACKUP" -nt "$LATEST_BACKUP" ]; then
        cp -f "$STAGED_BACKUP" "$LATEST_BACKUP"
        info "Бэкап скопирован в $LATEST_BACKUP"
    fi
fi

if [ -z "$LATEST_BACKUP" ] || [ ! -f "$LATEST_BACKUP" ]; then
    FOUND="$(find_latest_backup)"
    [ -n "$FOUND" ] && LATEST_BACKUP="$FOUND"
fi

if [ -n "$LATEST_BACKUP" ] && [ -f "$LATEST_BACKUP" ]; then
    echo ""
    question "Найден бэкап: $(basename "$LATEST_BACKUP") (источник: $LATEST_BACKUP). Восстановить БД? (y/N)"
    read -r restore_response
    if [[ "$restore_response" =~ ^[Yy]$ ]]; then
        if [ -f "scripts/restore.sh" ]; then
            echo "y" | bash scripts/restore.sh "$LATEST_BACKUP" || warn "Восстановление не удалось"
        else
            warn "scripts/restore.sh не найден"
        fi
    fi
else
    info "Файл бэкапа (*_backup_*.sql.gz) не найден в ${BACKUP_ROOT}/ или backups/ — пропуск restore."
fi

# --- Итог -------------------------------------------------------------------
info ""
info "=========================================="
info " Готово! Дальше — донастройка в боте:"
info "=========================================="
info "  • Откройте бота → «⚙️ Настройки»"
info "  • «📣 Каналы публикации» — Telegram/Max каналы"
info "  • «🔐 Интеграции и токены» — токены VK/Instagram/Max/Авито"
info "  • «🗂 Отчёты и списки» — получатели и ID сообщений"
info "  • «💾 Резервное копирование» — бэкап в Telegram по расписанию"
info ""
info "Обновление из бота (pull-модель): установите systemd timer на хосте:"
info "  sudo cp deploy/systemd/tg-poster-host-tasks.* /etc/systemd/system/"
info "  sudo systemctl daemon-reload && sudo systemctl enable --now tg-poster-host-tasks.timer"
info ""
info "Бэкап до деплоя: положите в ${BACKUP_ROOT}/ файл *_backup_*.sql.gz"
info "Логи приложения:  $DC logs -f app"
