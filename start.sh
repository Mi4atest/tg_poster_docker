#!/bin/bash

# Проверка наличия файла .env
if [ ! -f .env ]; then
    echo "Файл .env не найден. Копирование из .env.example..."
    cp .env.example .env
    echo "Пожалуйста, отредактируйте файл .env и запустите скрипт снова."
    exit 1
fi

# Запуск контейнеров
docker-compose up -d

echo "Контейнеры запущены!"
echo "API доступен по адресу: http://localhost:8002"
echo "Веб-интерфейс доступен по адресу: http://localhost:8080"
