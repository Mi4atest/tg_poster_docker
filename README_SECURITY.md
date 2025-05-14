# TG Poster - Безопасная Docker-версия

Безопасная Docker-версия системы для автоматизированного постинга контента в социальные сети (VK, Telegram, Instagram) через Telegram бота.

## Особенности безопасности

- **Изолированная сеть Docker** - все сервисы работают в изолированной сети
- **Закрытые порты** - доступ к базе данных и API закрыт извне
- **SSL/TLS шифрование** - все соединения защищены с использованием HTTPS
- **Web Application Firewall (ModSecurity)** - защита от распространенных атак
- **Ограничение доступа по IP** - только Telegram API может обращаться к webhook
- **Защита от SQL-инъекций** - использование ORM и параметризованных запросов
- **Резервное копирование** - автоматическое шифрованное резервное копирование

## Требования

- Ubuntu 20.04 или новее
- Доступ к серверу с правами sudo
- Доменное имя (для настройки webhook)

## Быстрая установка

### 1. Клонирование репозитория

```bash
git clone https://github.com/Mi4atest/tg_poster_docker.git
cd tg_poster_docker
git checkout security-improvements
```

### 2. Автоматическая установка

Для автоматической установки и настройки всех компонентов выполните:

```bash
./deploy.sh
```

Скрипт выполнит следующие действия:
- Установит необходимые зависимости (Docker, Docker Compose, UFW)
- Создаст файл .env из шаблона и предложит его отредактировать
- Сгенерирует SSL-сертификаты
- Настроит ModSecurity для защиты от атак
- Настроит брандмауэр (UFW)
- Запустит контейнеры
- Инициализирует базу данных
- Настроит автоматическое резервное копирование

### 3. Настройка переменных окружения

После запуска скрипта `deploy.sh` вам будет предложено отредактировать файл `.env`. Обязательно укажите следующие параметры:

```
# Telegram Bot
TELEGRAM_BOT_TOKEN=your_telegram_bot_token
ALLOWED_USER_IDS=123456789,987654321

# Telegram Channel
TELEGRAM_CHANNEL_ID=@your_channel_id

# Webhook (для Telegram)
USE_WEBHOOK=true
WEBHOOK_URL=https://your-domain.com/api/telegram/webhook
```

## Ручная установка

Если вы предпочитаете выполнить установку вручную, следуйте этим шагам:

### 1. Установка зависимостей

```bash
# Обновление списка пакетов
sudo apt-get update

# Установка необходимых пакетов
sudo apt-get install -y apt-transport-https ca-certificates curl gnupg lsb-release ufw openssl

# Установка Docker
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh
sudo usermod -aG docker $USER

# Установка Docker Compose
sudo curl -L "https://github.com/docker/compose/releases/download/v2.18.1/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
sudo chmod +x /usr/local/bin/docker-compose
```

### 2. Настройка файла .env

```bash
cp .env.example .env
nano .env
```

### 3. Генерация SSL-сертификатов

```bash
./generate_ssl.sh
```

### 4. Настройка ModSecurity

```bash
mkdir -p nginx/modsecurity
cp nginx/modsecurity/main.conf.example nginx/modsecurity/main.conf
```

### 5. Настройка брандмауэра

```bash
sudo ./setup_firewall.sh
```

### 6. Запуск контейнеров

```bash
docker-compose up -d
```

### 7. Инициализация базы данных

```bash
./init_db.sh
```

## Проверка установки

После установки вы можете проверить, что все компоненты работают корректно:

```bash
# Проверка статуса контейнеров
docker-compose ps

# Проверка логов
docker-compose logs

# Проверка доступности по HTTPS
curl -k https://localhost
```

## Резервное копирование и восстановление

### Создание резервной копии

```bash
./backup.sh
```

Резервные копии сохраняются в директории `backups` в зашифрованном виде.

### Восстановление из резервной копии

```bash
./restore.sh ./backups/backup_20230101_120000.sql.enc ./backups/media_20230101_120000.tar.gz.enc your_password
```

## Обновление

Для обновления проекта выполните:

```bash
git pull
docker-compose down
docker-compose build
docker-compose up -d
```

## Устранение неполадок

### Проблема: Контейнеры не запускаются

Проверьте логи:
```bash
docker-compose logs
```

### Проблема: Не работает SSL

Проверьте наличие сертификатов:
```bash
ls -la nginx/ssl
```

### Проблема: Бот не отвечает

Проверьте логи приложения:
```bash
docker-compose logs app
```

Убедитесь, что токен бота правильно указан в файле .env.

## Безопасность

### Обновление зависимостей

Регулярно обновляйте зависимости для устранения уязвимостей:

```bash
docker-compose pull
docker-compose build --no-cache
docker-compose up -d
```

### Обновление SSL-сертификатов

Для production-окружения рекомендуется использовать Let's Encrypt:

```bash
# Установка certbot
sudo apt-get install -y certbot

# Получение сертификата
sudo certbot certonly --standalone -d your-domain.com

# Копирование сертификатов
sudo cp /etc/letsencrypt/live/your-domain.com/fullchain.pem nginx/ssl/
sudo cp /etc/letsencrypt/live/your-domain.com/privkey.pem nginx/ssl/
sudo chmod 644 nginx/ssl/fullchain.pem
sudo chmod 600 nginx/ssl/privkey.pem
```

## Дополнительная информация

Для получения дополнительной информации о безопасности проекта см. файл [SECURITY.md](SECURITY.md).
