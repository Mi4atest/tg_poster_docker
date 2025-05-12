# TG Poster - Docker версия

Docker-версия системы для автоматизированного постинга контента в социальные сети (VK, Telegram, Instagram) через Telegram бота.

## Требования

- Docker
- Docker Compose

## Быстрый старт

1. Клонируйте репозиторий:
   ```bash
   git clone https://github.com/Mi4atest/tg_poster_docker.git
   cd tg_poster_docker
   ```
   
2. Уснатновите Docker:
   ```bash
   sudo apt update && sudo apt install -y docker.io docker-compose
   ```
   
3. Создайте файл .env на основе примера:
   ```bash
   cp .env.example .env
   ```

4. Отредактируйте файл .env, указав свои токены и настройки:
   ```bash
   nano .env
   ```

5. Запустите контейнеры:
   ```bash
   ./start.sh
   ```

6. Инициализируйте базу данных:
   ```bash
   ./init_db.sh
   ```

## Структура проекта

- `app/` - исходный код приложения
- `media/` - директория для медиа-файлов
- `migrations/` - миграции базы данных
- `nginx/` - конфигурация Nginx
- `Dockerfile` - инструкции для сборки Docker-образа
- `docker-compose.yml` - конфигурация Docker Compose
- `start.sh` - скрипт для запуска контейнеров
- `stop.sh` - скрипт для остановки контейнеров
- `logs.sh` - скрипт для просмотра логов
- `init_db.sh` - скрипт для инициализации базы данных

## Управление контейнерами

### Запуск контейнеров
```bash
./start.sh
```

### Остановка контейнеров
```bash
./stop.sh
```

### Просмотр логов
```bash
./logs.sh          # Все логи
./logs.sh app      # Только логи приложения
./logs.sh db       # Только логи базы данных
./logs.sh nginx    # Только логи Nginx
```

### Инициализация базы данных
```bash
./init_db.sh
```

## Доступ к приложению

- API: http://localhost:8002
- Веб-интерфейс: http://localhost:8080

## Настройка переменных окружения

Основные переменные окружения, которые нужно настроить в файле .env:

- `TELEGRAM_BOT_TOKEN` - токен Telegram бота
- `ALLOWED_USER_IDS` - список ID пользователей, которым разрешено использовать бота
- `VK_APP_ID`, `VK_APP_SECRET`, `VK_ACCESS_TOKEN`, `VK_GROUP_ID` - настройки для VK API
- `TELEGRAM_CHANNEL_ID` - ID канала Telegram для публикации
- `SECRET_KEY` - секретный ключ для JWT-токенов

## Работа с данными

### Резервное копирование базы данных
```bash
docker-compose exec db pg_dump -U postgres tg_poster > backup.sql
```

### Восстановление базы данных
```bash
cat backup.sql | docker-compose exec -T db psql -U postgres -d tg_poster
```

### Доступ к базе данных
```bash
docker-compose exec db psql -U postgres -d tg_poster
```

## Обновление приложения

1. Остановите контейнеры:
   ```bash
   ./stop.sh
   ```

2. Получите последние изменения:
   ```bash
   git pull
   ```

3. Пересоберите и запустите контейнеры:
   ```bash
   docker-compose build
   ./start.sh
   ```

## Устранение неполадок

### Проблема: Контейнеры не запускаются

Проверьте логи:
```bash
docker-compose logs
```

### Проблема: Бот не отвечает

Проверьте логи приложения:
```bash
./logs.sh app
```

Убедитесь, что токен бота правильно указан в файле .env.

### Проблема: Не работает подключение к базе данных

Проверьте, что контейнер с базой данных запущен:
```bash
docker-compose ps
```

Проверьте логи базы данных:
```bash
./logs.sh db
```
