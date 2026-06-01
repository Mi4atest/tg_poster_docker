#!/bin/bash
# Точка входа контейнера приложения.
# 1) ждём готовности БД, 2) создаём/обновляем схему и миграции, 3) запускаем приложение.
set -e

DB_URL="${DATABASE_URL:-postgresql://postgres:postgres@db:5432/tg_poster}"

echo "[entrypoint] Ожидание готовности базы данных..."
for i in $(seq 1 60); do
    if pg_isready -d "$DB_URL" >/dev/null 2>&1; then
        echo "[entrypoint] База данных доступна"
        break
    fi
    sleep 1
done

echo "[entrypoint] Инициализация схемы (create_all + ensure_database_schema)..."
python -c "from app.db.init_db import init_db; init_db()" || echo "[entrypoint] init_db: предупреждение (продолжаем)"

echo "[entrypoint] Применение миграций Alembic..."
alembic upgrade head 2>&1 || echo "[entrypoint] alembic: предупреждение (возможно, уже применены)"

echo "[entrypoint] Запуск приложения..."
exec python main.py
