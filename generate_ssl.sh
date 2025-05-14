#!/bin/bash

# Скрипт для генерации самоподписанных SSL-сертификатов

# Создаем директорию для сертификатов, если она не существует
mkdir -p nginx/ssl

# Генерируем приватный ключ и сертификат
openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
  -keyout nginx/ssl/privkey.pem \
  -out nginx/ssl/fullchain.pem \
  -subj "/C=RU/ST=State/L=City/O=Organization/CN=localhost"

# Устанавливаем правильные разрешения
chmod 600 nginx/ssl/privkey.pem
chmod 644 nginx/ssl/fullchain.pem

echo "SSL-сертификаты успешно сгенерированы в директории nginx/ssl/"
echo "Для использования в production рекомендуется заменить их на сертификаты от Let's Encrypt"
