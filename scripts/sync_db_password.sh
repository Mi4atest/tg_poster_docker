#!/bin/bash
# Синхронизирует пароль роли Postgres в существующем volume с DB_PASSWORD из .env.
#
# POSTGRES_PASSWORD в docker-compose действует только при первой инициализации volume.
# При повторном deploy.sh с новым .env приложение получает новый пароль, а БД — старый → H1.
#
# Восстановление через restore.sh идёт через local trust (psql без -h), поэтому дамп
# заливается, а app падает по сети с новым паролем — этот скрипт чинит оба случая.
#
# Использование:
#   bash scripts/sync_db_password.sh [--restart-app]
set -e

cd "$(dirname "$0")/.."

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info() { echo -e "${GREEN}[INFO]${NC} $1"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }

if [ ! -f ".env" ]; then
    warn "sync_db_password: .env не найден, пропуск"
    exit 0
fi
# shellcheck disable=SC1091
source .env

DB_USER="${DB_USER:-postgres}"
DB_PASSWORD="${DB_PASSWORD:-postgres}"
DB_NAME="${DB_NAME:-tg_poster}"

if docker compose version >/dev/null 2>&1; then
    DC="docker compose"
else
    DC="docker-compose"
fi

if ! $DC ps --status running 2>/dev/null | grep -qE 'tg_poster_db|db'; then
    if ! docker ps --format '{{.Names}}' 2>/dev/null | grep -q 'tg_poster_db'; then
        warn "sync_db_password: контейнер db не запущен, пропуск"
        exit 0
    fi
fi

info "Ожидание готовности Postgres..."
ready=0
for _ in $(seq 1 60); do
    if $DC exec -T db pg_isready -U "$DB_USER" -d "$DB_NAME" >/dev/null 2>&1; then
        ready=1
        break
    fi
    sleep 1
done
if [ "$ready" -ne 1 ]; then
    warn "sync_db_password: Postgres не ответил за 60 с"
    exit 1
fi

# Экранирование одинарных кавычек в пароле для SQL
esc_pass="${DB_PASSWORD//\'/\'\'}"

info "Синхронизация пароля пользователя ${DB_USER} с .env (ALTER USER)..."
if ! $DC exec -T db psql -v ON_ERROR_STOP=1 -U "$DB_USER" -d postgres <<EOSQL
ALTER USER "${DB_USER}" WITH PASSWORD '${esc_pass}';
EOSQL
then
    warn "sync_db_password: ALTER USER не выполнен"
    exit 1
fi

# Проверка: подключение по сети с паролем из .env (как у приложения)
if $DC exec -T -e PGPASSWORD="$DB_PASSWORD" db \
    psql -h localhost -U "$DB_USER" -d "$DB_NAME" -c "SELECT 1" >/dev/null 2>&1; then
    info "Проверка подключения с паролем из .env: OK"
else
    warn "Проверка подключения с паролем из .env не прошла (возможны отличия pg_hba)"
fi

if [[ "${1:-}" == "--restart-app" ]]; then
    info "Перезапуск контейнера app..."
    $DC restart app 2>/dev/null || $DC up -d app
fi
