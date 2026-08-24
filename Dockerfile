# --- builder: компилятор и dev-заголовки только для pip install ---
FROM python:3.10-slim-bookworm AS builder

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    python3-dev \
    libpq-dev \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt requirements.lock ./

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.lock


# --- runtime: без gcc; git для update из бота; compose — с хоста (/usr/bin/docker-compose) ---
FROM python:3.10-slim-bookworm AS runtime

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    postgresql-client \
    fonts-dejavu-core \
    git \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    API_HOST=0.0.0.0 \
    API_PORT=8002 \
    INSTAGRAM_GRAPH_API_VERSION=v19.0 \
    INSTAGRAM_GRAPH_TIMEOUT_SECONDS=60 \
    INSTAGRAM_GRAPH_REFRESH_BEFORE_DAYS=7 \
    INSTAGRAM_GRAPH_TOKEN_DAILY_CHECK_INTERVAL_SECONDS=86400 \
    INSTAGRAM_STORY_MODE=disabled

COPY . .

RUN mkdir -p /app/media /app/backups && chmod 777 /app/media /app/backups \
    && chmod +x /app/entrypoint.sh

EXPOSE 8002

ENTRYPOINT ["/app/entrypoint.sh"]
