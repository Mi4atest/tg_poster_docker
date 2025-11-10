#!/bin/bash

echo "Проверка и применение миграций базы данных..."

# Проверяем и применяем необходимые изменения схемы
docker-compose exec app python -c "from app.db.migrate import ensure_database_schema; ensure_database_schema()"

# Применяем миграции Alembic (если таблица alembic_version существует)
echo "Применение миграций Alembic..."
docker-compose exec app alembic upgrade head 2>&1 || echo "Alembic миграции не применены (возможно, таблица alembic_version не существует или уже применена)"

echo "Миграции применены!"

