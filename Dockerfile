FROM python:3.10-slim-bullseye

# Установка рабочей директории
WORKDIR /app

# Установка зависимостей
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    python3-dev \
    libpq-dev \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Копирование файлов зависимостей
COPY requirements.txt .

# Установка зависимостей Python
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Копирование исходного кода
COPY . .

# Создание директории для медиа-файлов
RUN mkdir -p /app/media && chmod 777 /app/media

# Переменные окружения по умолчанию
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DATABASE_URL=postgresql://postgres:postgres@db:5432/tg_poster \
    API_HOST=0.0.0.0 \
    API_PORT=8002

# Открытие порта
EXPOSE 8002

# Запуск приложения
CMD ["python", "main.py"]
