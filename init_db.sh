#!/bin/bash

# Инициализация базы данных
echo "Инициализация базы данных..."
docker-compose exec app python -c "from app.db.database import Base, engine; from app.api.models.post import Post, PublicationLog; from app.api.models.story import Story, StoryPublicationLog; Base.metadata.create_all(bind=engine)"

echo "Проверка и применение миграций..."
docker-compose exec app python -c "from app.db.migrate import ensure_database_schema; ensure_database_schema()"

echo "База данных инициализирована!"
