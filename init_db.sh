#!/bin/bash

# Инициализация базы данных
echo "Инициализация базы данных..."
docker-compose exec app python -c "from app.db.database import Base, engine; from app.api.models.post import Post, PublicationLog; from app.api.models.story import Story, StoryPublicationLog; Base.metadata.create_all(bind=engine)"
echo "База данных инициализирована!"
