#!/bin/bash
# Восстановление базы данных из резервной копии (*.sql или *.sql.gz).
# Использование: bash scripts/restore.sh [путь_к_бэкапу]
# Без аргумента берётся самый свежий бэкап из backups/.
set -e

# Корень проекта (скрипт лежит в scripts/)
cd "$(dirname "$0")/.."

if [ -f ".env" ]; then
    # shellcheck disable=SC1091
    source .env
fi

if docker compose version >/dev/null 2>&1; then DC="docker compose"; else DC="docker-compose"; fi

DB_NAME="${DB_NAME:-tg_poster}"
DB_USER="${DB_USER:-postgres}"

if [ -z "$1" ]; then
    echo "Путь не указан, ищу последний бэкап в backups/ и ${TG_POSTER_BACKUP_ROOT:-/root}/..."
    BACKUP_FILE=""
  for search_dir in backups "${TG_POSTER_BACKUP_ROOT:-/root}"; do
    [ -d "$search_dir" ] || continue
    candidate=$(find "$search_dir" -maxdepth 1 -name "*_backup_*.gz" -type f -printf "%T@ %p\n" 2>/dev/null | sort -nr | head -n1 | cut -d' ' -f2-)
    if [ -n "$candidate" ]; then
      if [ -z "$BACKUP_FILE" ] || [ "$candidate" -nt "$BACKUP_FILE" ]; then
        BACKUP_FILE="$candidate"
      fi
    fi
  done
    [ -z "$BACKUP_FILE" ] && { echo "Бэкапы не найдены (*_backup_*.gz в backups/ или /root)"; exit 1; }
    echo "Найден: $BACKUP_FILE"
else
    BACKUP_FILE="$1"
fi

[ ! -f "$BACKUP_FILE" ] && { echo "Файл '$BACKUP_FILE' не существует"; exit 1; }

TEMP_FILE=""
if [[ "$BACKUP_FILE" == *.gz ]]; then
    TEMP_FILE="${BACKUP_FILE%.gz}"
    gunzip -c "$BACKUP_FILE" > "$TEMP_FILE"
    BACKUP_FILE="$TEMP_FILE"
fi

echo "ВНИМАНИЕ: текущая база '$DB_NAME' будет перезаписана!"
read -rp "Продолжить? (y/N) " confirm
if [[ ! "$confirm" =~ ^[Yy]$ ]]; then
    echo "Отменено."
    [ -n "$TEMP_FILE" ] && [ -f "$TEMP_FILE" ] && rm -f "$TEMP_FILE"
    exit 0
fi

echo "Очистка схемы..."
$DC exec -T db psql -U "$DB_USER" -d "$DB_NAME" <<EOF
SELECT pg_terminate_backend(pid) FROM pg_stat_activity
 WHERE datname = '$DB_NAME' AND pid <> pg_backend_pid();
DROP SCHEMA public CASCADE;
CREATE SCHEMA public;
GRANT ALL ON SCHEMA public TO $DB_USER;
GRANT ALL ON SCHEMA public TO public;
EOF

echo "Восстановление данных..."
cat "$BACKUP_FILE" | $DC exec -T db psql -U "$DB_USER" -d "$DB_NAME"

echo "✅ Восстановление завершено."
[ -n "$TEMP_FILE" ] && [ -f "$TEMP_FILE" ] && rm -f "$TEMP_FILE"
