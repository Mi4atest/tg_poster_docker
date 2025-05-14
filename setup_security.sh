#!/bin/bash

# Скрипт для настройки безопасности проекта

echo "Настройка безопасности проекта..."

# Генерация SSL-сертификатов
if [ ! -f nginx/ssl/fullchain.pem ] || [ ! -f nginx/ssl/privkey.pem ]; then
    echo "Генерация SSL-сертификатов..."
    ./generate_ssl.sh
fi

# Создание директории для ModSecurity
if [ ! -d nginx/modsecurity ]; then
    echo "Создание директории для ModSecurity..."
    mkdir -p nginx/modsecurity
fi

# Проверка наличия файла конфигурации ModSecurity
if [ ! -f nginx/modsecurity/main.conf ]; then
    echo "Файл конфигурации ModSecurity не найден. Пожалуйста, создайте его вручную."
    exit 1
fi

# Обновление переменных окружения
if [ ! -f .env ]; then
    echo "Создание файла .env из шаблона..."
    cp .env.example .env
    
    # Генерация случайного пароля для базы данных
    DB_PASSWORD=$(openssl rand -base64 12)
    
    # Генерация секретного ключа
    SECRET_KEY=$(openssl rand -base64 32)
    
    # Обновление файла .env
    sed -i "s/DB_PASSWORD=.*/DB_PASSWORD=$DB_PASSWORD/" .env
    sed -i "s/SECRET_KEY=.*/SECRET_KEY=$SECRET_KEY/" .env
    
    echo "Файл .env создан и обновлен с безопасными значениями."
else
    echo "Файл .env уже существует. Пропускаем создание."
fi

echo "Настройка безопасности завершена."
echo "Для применения изменений перезапустите контейнеры:"
echo "./stop.sh && ./start.sh"
