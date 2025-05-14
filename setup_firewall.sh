#!/bin/bash

# Скрипт для настройки брандмауэра (UFW)

echo "Настройка брандмауэра (UFW)..."

# Проверка наличия UFW
if ! command -v ufw &> /dev/null; then
    echo "UFW не установлен. Установка..."
    apt-get update
    apt-get install -y ufw
fi

# Сброс правил UFW
echo "Сброс правил UFW..."
ufw --force reset

# Установка политики по умолчанию
echo "Установка политики по умолчанию..."
ufw default deny incoming
ufw default allow outgoing

# Разрешение SSH (для удаленного доступа)
echo "Разрешение SSH..."
ufw allow ssh

# Разрешение HTTP и HTTPS
echo "Разрешение HTTP и HTTPS..."
ufw allow 80/tcp
ufw allow 443/tcp

# Разрешение доступа только с IP-адресов Telegram для webhook
echo "Разрешение доступа только с IP-адресов Telegram для webhook..."
for ip in 149.154.160.0/20 91.108.4.0/22; do
    ufw allow from $ip to any port 443 proto tcp
done

# Блокировка всех остальных портов
echo "Блокировка всех остальных портов..."
ufw deny 8002/tcp  # API
ufw deny 5432/tcp  # PostgreSQL
ufw deny 8080/tcp  # Nginx (старый порт)

# Включение UFW
echo "Включение UFW..."
ufw --force enable

echo "Настройка брандмауэра завершена."
echo "Текущие правила UFW:"
ufw status verbose
