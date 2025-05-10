#!/bin/bash

# Инициализация базы данных
echo "Инициализация базы данных..."
docker-compose exec app python -c "from app.db.database import Base, engine; from app.db.models import *; Base.metadata.create_all(bind=engine)"
echo "База данных инициализирована!"
