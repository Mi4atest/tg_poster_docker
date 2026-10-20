# VK: несколько копий проекта (appleshop / mobi / moto)

## Модель

Одно одобренное VK-приложение → несколько независимых инстансов бота.

На каждом инстансе:

- свой `PUBLIC_BASE_URL` (поддомен);
- свой `VK_GROUP_ID`;
- свой `VK_OAUTH_OWNER_ID` (VK user_id редактора/админа **этой** группы);
- своя БД Postgres, `MASTER_KEY`, Telegram-бот;
- своя OAuth-сессия (access + refresh + device_id) — **не копировать** между серверами.

Ссылка входа уникальна для копии:

`https://<поддомен>/vk/oauth/vkid/start`

## Кабинет VK (один раз)

В Redirect URI одного приложения добавьте все callback:

- `https://appleshop.ap43.ru/vk/oauth/callback`
- `https://mobi.ap43.ru/vk/oauth/callback`
- `https://moto.ap43.ru/vk/oauth/callback`

## .env на каждом инстансе

```bash
PUBLIC_BASE_URL=https://mobi.ap43.ru
VK_APP_ID=54604726
VK_APP_SECRET=...
VK_GROUP_ID=<id группы этого магазина>
VK_OAUTH_OWNER_ID=<vk user_id владельца токена>
VK_API_MIN_INTERVAL_SEC=0.35
VK_QUOTA_ALERT_ENABLED=true
```

Затем назначенный пользователь проходит OAuth на **своём** поддомене.

## Nginx

Примеры:

- `nginx/host-appleshop.ap43.ru.conf.example` → порт `8080`
- `nginx/host-mobi.ap43.ru.conf.example` → порт `8081`
- `nginx/host-moto.ap43.ru.conf.example` → порт `8082`

У каждого compose-стека свой опубликованный порт nginx и отдельные volumes.

## Лимиты API

Квота VK на **приложение** общая. Центральный координатор пока не обязателен:

- у каждого инстанса локальный flood-gate (3 ч) и мин. интервал вызовов;
- тяжёлые пакеты (массовые цены / импорт каталога) не запускайте одновременно на всех копиях;
- при code 9/29 админы получают Telegram-алерт.

## Проверка после привязки

1. Чекер токена в боте — все нужные методы зелёные.
2. Тест: `market.get`, пост с фото, сторис, смена цены, комментарий «неактуально» — только в группе инстанса.
3. Через ~50–60 мин в логах: `VK ID token refreshed`.
