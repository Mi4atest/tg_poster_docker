#!/bin/bash
# Остановка контейнеров. Использование: bash scripts/stop.sh
cd "$(dirname "$0")/.."
if docker compose version >/dev/null 2>&1; then DC="docker compose"; else DC="docker-compose"; fi
$DC down
echo "Контейнеры остановлены."
