#!/bin/bash
# =============================================================================
#  tg_poster — единый скрипт развёртывания и обновления
#
#  Установка/обновление одной командой:
#    bash <(wget -qO- --no-hsts --inet4-only \
#      https://raw.githubusercontent.com/Mi4atest/tg_poster_docker/Test_planner/deploy.sh)
#
#  Что делает:
#    1) Проверяет окружение (git, docker, docker compose)
#    2) Клонирует или обновляет проект из GitHub
#    3) При первом запуске — мастер .env (минимум данных, остальное в боте)
#    4) Собирает и запускает контейнеры; схема БД и миграции применяются
#       автоматически в entrypoint контейнера
#    5) Предлагает восстановление из бэкапа, если он есть
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

# --- 1. Префлайт ------------------------------------------------------------
info "=========================================="
info " Развёртывание tg_poster (ветка: $BRANCH)"
info "=========================================="

command -v git >/dev/null 2>&1 || { error "git не установлен"; exit 1; }
command -v docker >/dev/null 2>&1 || { error "docker не установлен"; exit 1; }

if docker compose version >/dev/null 2>&1; then
    DC="docker compose"
elif command -v docker-compose >/dev/null 2>&1; then
    DC="docker-compose"
else
    error "docker compose не найден (нужен docker compose v2 или docker-compose)"
    exit 1
fi
info "Используется: $DC"

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
    warn "Это нормально для исходного сервера. Для синхронизации с GitHub см. scripts/sync_to_github.sh"
    cd "$PROJECT_DIR"
else
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

    read -rp "$(echo -e "${BLUE}[?]${NC} ") ID администраторов через запятую (ALLOWED_USER_IDS): " ADMIN_IDS
    while [ -z "$ADMIN_IDS" ]; do
        read -rp "    Нужен хотя бы один ID. Введите ALLOWED_USER_IDS: " ADMIN_IDS
    done

    # Автогенерация секретов
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
ALLOWED_USER_IDS=${ADMIN_IDS}
MASTER_KEY=${MASTER_KEY}

DB_USER=postgres
DB_PASSWORD=${DB_PASSWORD}
DB_NAME=tg_poster
DATABASE_URL=${DATABASE_URL}

# Токены площадок (можно оставить пустыми и заполнить в боте → «Настройки → Интеграции»)
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
    # Гарантируем наличие MASTER_KEY (нужен для шифрования секретов в БД)
    if ! grep -qE '^MASTER_KEY=.+' .env; then
        if command -v openssl >/dev/null 2>&1; then
            NEW_KEY="$(openssl rand -hex 32)"
        else
            NEW_KEY="$(head -c32 /dev/urandom | od -An -tx1 | tr -d ' \n')"
        fi
        # удаляем пустой MASTER_KEY= если он был, и дописываем заполненный
        sed -i '/^MASTER_KEY=$/d' .env
        echo "MASTER_KEY=${NEW_KEY}" >> .env
        warn "В .env добавлен сгенерированный MASTER_KEY (шифрование секретов включено)."
    fi
fi

# --- 4. Сборка и запуск -----------------------------------------------------
info ""
info "Сборка образов..."
$DC build

info "Запуск контейнеров (миграции применятся автоматически в entrypoint)..."
$DC up -d

info "Статус контейнеров:"
$DC ps

# --- 5. Восстановление из бэкапа (опционально) ------------------------------
if [ -d "backups" ]; then
    LATEST_BACKUP=$(find backups -name "*_backup_*.gz" -type f -printf "%T@ %p\n" 2>/dev/null | sort -nr | head -n1 | cut -d' ' -f2-)
    if [ -n "$LATEST_BACKUP" ]; then
        echo ""
        question "Найден бэкап: $(basename "$LATEST_BACKUP"). Восстановить БД? (y/N)"
        read -r restore_response
        if [[ "$restore_response" =~ ^[Yy]$ ]]; then
            if [ -f "scripts/restore.sh" ]; then
                echo "y" | bash scripts/restore.sh "$LATEST_BACKUP" || warn "Восстановление не удалось"
            else
                warn "scripts/restore.sh не найден"
            fi
        fi
    fi
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
info "Логи приложения:  $DC logs -f app"
