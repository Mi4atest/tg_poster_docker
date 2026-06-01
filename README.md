# TG Poster - Docker версия

Docker-версия системы для автоматизированного постинга контента в социальные сети (VK, Telegram, Instagram) через Telegram бота.

## Требования

- Docker
- Docker Compose

## Быстрый старт (одна команда)

На сервере с установленными `git`, `docker` и `docker compose`:

```bash
bash <(wget -qO- --no-hsts --inet4-only https://raw.githubusercontent.com/Mi4atest/tg_poster_docker/Test_planner/deploy.sh)
```

`deploy.sh` склонирует/обновит проект, при первом запуске спросит только
`TELEGRAM_BOT_TOKEN` и `ALLOWED_USER_IDS` (`MASTER_KEY` и пароль БД сгенерирует сам),
соберёт и запустит контейнеры. Схема БД и миграции применяются автоматически в
`entrypoint` контейнера. Остальное настраивается в боте → «⚙️ Настройки».

Подробнее: см. `DEPLOY_INSTRUCTIONS.md` и `QUICK_DEPLOY_GUIDE.md`.

## Структура проекта

- `app/` - исходный код приложения
- `media/` - директория для медиа-файлов
- `migrations/` - миграции базы данных
- `nginx/` - конфигурация Nginx
- `backups/` - директория для резервных копий базы данных
- `Dockerfile` - сборка образа
- `entrypoint.sh` - ожидание БД + авто-инициализация/миграции при старте
- `docker-compose.yml` - конфигурация Docker Compose
- `deploy.sh` - установка/обновление одной командой
- `scripts/sync_to_github.sh` - публикация изменений сервер → GitHub
- `scripts/{start,stop,logs,restore}.sh` - операционные команды

## Управление контейнерами

```bash
bash scripts/start.sh        # запуск
bash scripts/stop.sh         # остановка
bash scripts/logs.sh app     # логи приложения (db / nginx — другие сервисы)
```

### Инициализация и миграции БД

Выполняются автоматически в `entrypoint.sh` при старте контейнера
(ожидание БД → `init_db` → `alembic upgrade head`). Вручную обычно не требуется.

## Доступ к приложению

- API: http://localhost:8002
- Веб-интерфейс: http://localhost:8080

## Настройка переменных окружения

В `.env` держим только «загрузочный» минимум:

- `TELEGRAM_BOT_TOKEN` - токен бота управления
- `ALLOWED_USER_IDS` - кому разрешён доступ к боту
- `MASTER_KEY` - ключ шифрования секретов в БД (генерируется один раз)
- `DB_USER`, `DB_PASSWORD`, `DB_NAME`, `DATABASE_URL` - доступ к БД (используются docker-compose и приложением)
- токены площадок VK / Instagram / Max — пока читаются из `.env`, но их можно
  оставить пустыми и ввести в боте (хранятся зашифрованными).

Всё остальное (каналы, отчёты/списки, бэкап, подписи/контакты, токены интеграций)
настраивается в боте → «⚙️ Настройки» и хранится в БД. Шаблон — `.env.example`.

> При первом старте значения из `.env` один раз импортируются в БД (bootstrap);
> после проверки в боте лишние строки из `.env` можно удалить.

## Работа с данными

### Доступ к базе данных
```bash
docker compose exec db psql -U "$DB_USER" -d "$DB_NAME"
```

## Система резервного копирования

Резервное копирование выполняется **внутри приложения** (хостовый cron не нужен) и
настраивается в боте → «⚙️ Настройки → 💾 Резервное копирование»:

- токен бота бэкапа (хранится зашифрованным), chat_id, имя проекта, медиа вкл/выкл;
- время запуска и переключатель автобэкапа;
- кнопка «📦 Сделать бэкап сейчас».

Дамп БД создаётся через `pg_dump`, сжимается и отправляется в Telegram. Старые копии
(> N дней) удаляются. Восстановление из бэкапа:

```bash
bash scripts/restore.sh путь/к/файлу/резервной_копии.sql.gz
# без аргумента — берётся самый свежий бэкап из backups/
```

### Ручное резервное копирование (альтернативный способ)
```bash
docker-compose exec db pg_dump -U postgres tg_poster > backup.sql
```

### Ручное восстановление (альтернативный способ)
```bash
cat backup.sql | docker-compose exec -T db psql -U postgres -d tg_poster
```

## Обновление приложения

Одной командой (см. «Быстрый старт»):

```bash
bash <(wget -qO- --no-hsts --inet4-only https://raw.githubusercontent.com/Mi4atest/tg_poster_docker/Test_planner/deploy.sh)
```

Или вручную в каталоге проекта:

```bash
git pull
docker compose build
docker compose up -d
```

## Миграции базы данных

Схема БД проверяется и обновляется **автоматически при старте контейнера**
(`entrypoint.sh`: ожидание БД → `init_db` (create_all + ensure_database_schema)
→ `alembic upgrade head`). Ручные шаги обычно не нужны.

### Применение миграций вручную (если требуется)

```bash
docker compose exec app python -c "from app.db.init_db import init_db; init_db()"
docker compose exec app alembic current      # текущая ревизия
docker compose exec app alembic upgrade head # применить все
docker compose exec app alembic history      # история
```

### Проверка состояния базы данных

Проверить наличие колонки `telegram_link`:

```bash
docker-compose exec db psql -U postgres -d tg_poster -c "\d posts"
```

Или через Python:

```bash
docker-compose exec app python -c "from app.db.database import engine; from sqlalchemy import inspect; inspector = inspect(engine); columns = [col['name'] for col in inspector.get_columns('posts')]; print('telegram_link' in columns)"
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
bash scripts/logs.sh app
```

Убедитесь, что токен бота правильно указан в файле .env.

### Проблема: Не работает подключение к базе данных

Проверьте, что контейнер с базой данных запущен:
```bash
docker-compose ps
```

Проверьте логи базы данных:
```bash
bash scripts/logs.sh db
```
