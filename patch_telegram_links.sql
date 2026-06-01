-- Ручная инъекция: привязка 4 товаров к ссылкам на посты в Telegram.
-- Таблицы: products, posts. Связь: products.post_id = posts.id.
--
-- Запуск с хоста:
--   docker-compose exec -T db psql -U postgres -d tg_poster < patch_telegram_links.sql
--
-- Или из контейнера db (если файл смонтирован):
--   psql -U postgres -d tg_poster -f /path/to/patch_telegram_links.sql

BEGIN;

-- 1) Обновить products.telegram_link по коду в названии (5969, 8710, 0511, 0605)
UPDATE products SET telegram_link = v.link
FROM (VALUES
  ('%5969%', 'https://t.me/AppleShop43/7547'),
  ('%8710%', 'https://t.me/AppleShop43/7872'),
  ('%0511%', 'https://t.me/AppleShop43/8304'),
  ('%0605%', 'https://t.me/AppleShop43/8175')
) AS v(pat, link)
WHERE products.name LIKE v.pat AND products.status = 'active';

-- 2) Обновить связанные посты: posts.telegram_link и is_published_telegram
UPDATE posts SET telegram_link = pr.telegram_link, is_published_telegram = true
FROM products pr
WHERE posts.id = pr.post_id
  AND pr.telegram_link IN (
    'https://t.me/AppleShop43/7547',
    'https://t.me/AppleShop43/7872',
    'https://t.me/AppleShop43/8304',
    'https://t.me/AppleShop43/8175'
  );

COMMIT;
