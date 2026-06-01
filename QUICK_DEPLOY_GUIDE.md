# Быстрый старт: развёртывание и обновление

## Установка / обновление одной командой

На целевом сервере (нужны установленные `git`, `docker`, `docker compose`):

```bash
bash <(wget -qO- --no-hsts --inet4-only https://raw.githubusercontent.com/Mi4atest/tg_poster_docker/Test_planner/deploy.sh)
```

`deploy.sh` сам:
1. Проверит окружение (git, docker, docker compose).
2. Склонирует или обновит проект из GitHub.
3. При первом запуске запустит мастер `.env` — спросит только:
   - `TELEGRAM_BOT_TOKEN` (токен бота управления),
   - `ALLOWED_USER_IDS` (кому доступен бот),
   - а `MASTER_KEY` и пароль БД сгенерирует сам.
4. Соберёт и запустит контейнеры. **Схема БД и миграции применяются автоматически** в `entrypoint` контейнера (ждёт БД → `init_db` → `alembic upgrade head`).
5. Если есть бэкап в `backups/`, предложит восстановление.

## Что настраивается в боте (не в .env)

После запуска откройте бота → «⚙️ Настройки»:
- 📣 **Каналы публикации** — Telegram/Max каналы;
- 🔐 **Интеграции и токены** — токены VK / Instagram / Max / Авито (хранятся зашифрованными);
- 🗂 **Отчёты и списки** — получатели отчётов VK, ID сообщений «Наличие» и «Список б/у»;
- 💾 **Резервное копирование** — токен/чат/время; бэкап делается внутри приложения и шлётся в Telegram (хостовый cron не нужен);
- 📇 **Контакты и подписи** — подписи и контакты.

## Синхронизация изменений сервер → GitHub

Если правили код на сервере и хотите выложить в репозиторий (чтобы потом обновлять другие серверы через `deploy.sh`):

```bash
git config user.name "Your Name"      # один раз, если не настроено
git config user.email "you@example.com"
bash scripts/sync_to_github.sh "описание изменений"
```

`.env` и runtime-данные в коммит не попадают (см. `.gitignore`).

## Операционные команды

```bash
bash scripts/logs.sh app     # логи приложения
bash scripts/start.sh        # поднять контейнеры
bash scripts/stop.sh         # остановить
bash scripts/restore.sh      # восстановить БД из последнего бэкапа
```

## Проверка успешного запуска

В логах (`bash scripts/logs.sh app`) должны быть:
```
Publication orchestrator started
Started worker for platform: vk
Started worker for platform: telegram
Backup scheduler started
```
