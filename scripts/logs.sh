#!/bin/bash
# Просмотр логов контейнеров. Использование: bash scripts/logs.sh [service]
cd "$(dirname "$0")/.."
if docker compose version >/dev/null 2>&1; then DC="docker compose"; else DC="docker-compose"; fi
if [ -z "$1" ]; then
    $DC logs -f
else
    $DC logs -f "$1"
fi
