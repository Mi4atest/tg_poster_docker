FROM python:3.10-slim-bookworm

# Установка рабочей директории
WORKDIR /app

# Установка зависимостей (postgresql-client нужен для pg_dump/pg_isready — бэкап и entrypoint)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    python3-dev \
    libpq-dev \
    postgresql-client \
    fonts-dejavu-core \
    git \
    docker-compose \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Копирование файлов зависимостей (lock = полный pin транзитивных версий)
COPY requirements.txt requirements.lock ./

# Установка зависимостей Python строго по lock (без дрейфа при rebuild)
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.lock

# Копирование исходного кода
COPY . .

# Создание директорий для медиа-файлов и бэкапов + entrypoint
RUN mkdir -p /app/media /app/backups && chmod 777 /app/media /app/backups \
    && chmod +x /app/entrypoint.sh

# Переменные окружения по умолчанию
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    API_HOST=0.0.0.0 \
    API_PORT=8002 \
    INSTAGRAM_GRAPH_API_VERSION=v19.0 \
    INSTAGRAM_GRAPH_TIMEOUT_SECONDS=60 \
    INSTAGRAM_GRAPH_REFRESH_BEFORE_DAYS=7 \
    INSTAGRAM_GRAPH_TOKEN_DAILY_CHECK_INTERVAL_SECONDS=86400 \
    INSTAGRAM_STORY_MODE=disabled

# Открытие порта
EXPOSE 8002

# Запуск приложения: ожидание БД + миграции + старт (см. entrypoint.sh)
ENTRYPOINT ["/app/entrypoint.sh"]
