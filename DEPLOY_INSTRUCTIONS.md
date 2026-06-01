# Развёртывание проекта

## 1. Установка / обновление одной командой (рекомендуется)

```bash
bash <(wget -qO- --no-hsts --inet4-only https://raw.githubusercontent.com/Mi4atest/tg_poster_docker/Test_planner/deploy.sh)
```

или через curl:

```bash
bash <(curl -sSL https://raw.githubusercontent.com/Mi4atest/tg_poster_docker/Test_planner/deploy.sh)
```

Скрипт `deploy.sh`:
- клонирует/обновляет код из ветки `Test_planner`;
- при первом запуске создаёт `.env` (спрашивает только токен бота и список админов, остальное генерирует/настраивается в боте);
- собирает и запускает контейнеры;
- **миграции применяются автоматически** при старте контейнера (см. `entrypoint.sh`).

Параметры можно переопределить переменными окружения:
`TG_POSTER_REPO`, `TG_POSTER_BRANCH`, `TG_POSTER_DIR`.

## 2. Архитектура конфигурации

- **`.env`** — только «загрузочный» минимум: `TELEGRAM_BOT_TOKEN`, `ALLOWED_USER_IDS`,
  `MASTER_KEY`, доступ к БД (`DB_*`, `DATABASE_URL`) и (временно) токены площадок.
- **БД + меню «⚙️ Настройки»** — каналы, отчёты/списки, бэкап, подписи/контакты, токены интеграций.
  Секреты хранятся зашифрованными (Fernet, ключ — `MASTER_KEY`).
- При первом старте значения из `.env` один раз импортируются в БД (bootstrap). После
  проверки в боте лишние строки из `.env` можно удалить.

## 3. Ручные шаги (если нужно)

```bash
cd /root/tg_poster_docker
docker compose build
docker compose up -d
docker compose logs -f app
```

Миграции применять вручную обычно не нужно — это делает `entrypoint.sh`. При необходимости:

```bash
docker compose exec app python -c "from app.db.init_db import init_db; init_db()"
docker compose exec app alembic upgrade head
```

## 4. Откат

```bash
git checkout <предыдущая-ревизия>
docker compose up -d --build
```

(Данные БД сохраняются в docker volume `postgres_data` и при пересборке не теряются.)

## 5. Проверка успешного запуска

В логах должны быть:
```
Publication orchestrator started
Started worker for platform: vk
Started worker for platform: telegram
Backup scheduler started
```
