#!/bin/bash
# Запуск контейнеров. Использование: bash scripts/start.sh
cd "$(dirname "$0")/.."
if [ ! -f .env ]; then
    echo "Файл .env не найден. Запустите deploy.sh для первичной настройки."
    exit 1
fi
if docker compose version >/dev/null 2>&1; then DC="docker compose"; else DC="docker-compose"; fi
$DC up -d
echo "Контейнеры запущены. Веб-интерфейс (локально): http://127.0.0.1:8080"
