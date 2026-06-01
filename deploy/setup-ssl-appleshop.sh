#!/bin/bash
# Настройка HTTPS для appleshop.ap43.ru на сервере с tg_poster_docker + (опционально) AntiZapret.
# Запуск: sudo bash deploy/setup-ssl-appleshop.sh
set -euo pipefail

DOMAIN="appleshop.ap43.ru"
PROJECT_DIR="${PROJECT_DIR:-/root/tg_poster_docker}"
WEBROOT="/var/www/certbot"

echo "==> Проверка DNS (должен быть 147.45.117.60 или IP этого сервера)"
getent hosts "$DOMAIN" || true

echo "==> Кто слушает 80 и 443"
ss -tlnp | grep -E ':80 |:443 ' || true

echo "==> Публикация tg_poster nginx только на localhost:8080"
cd "$PROJECT_DIR"
if ! grep -q '127.0.0.1:8080:80' docker-compose.yml 2>/dev/null; then
  echo "Добавьте в docker-compose.yml для сервиса nginx:"
  echo '    ports:'
  echo '      - "127.0.0.1:8080:80"'
  echo "Затем: docker compose up -d nginx"
  exit 1
fi
docker compose up -d nginx

apt-get update -qq
apt-get install -y nginx certbot python3-certbot-nginx

mkdir -p "$WEBROOT"
cp -f "$PROJECT_DIR/nginx/host-appleshop.ap43.ru.conf.example" \
  "/etc/nginx/sites-available/${DOMAIN}.conf"

# Временно без SSL — только :80 для certbot
sed -i 's/listen 443/#listen 443/g' "/etc/nginx/sites-available/${DOMAIN}.conf"
sed -i 's/ssl_certificate/#ssl_certificate/g' "/etc/nginx/sites-available/${DOMAIN}.conf"
ln -sf "/etc/nginx/sites-available/${DOMAIN}.conf" "/etc/nginx/sites-enabled/${DOMAIN}.conf"
nginx -t && systemctl reload nginx

certbot certonly --webroot -w "$WEBROOT" -d "$DOMAIN" \
  --agree-tos --register-unsafely-without-email --non-interactive || {
  echo "Если certbot упал: проверьте, что порт 80 свободен и DNS указывает на этот сервер."
  exit 1
}

cp -f "$PROJECT_DIR/nginx/host-appleshop.ap43.ru.conf.example" \
  "/etc/nginx/sites-available/${DOMAIN}.conf"
ln -sf "/etc/nginx/sites-available/${DOMAIN}.conf" "/etc/nginx/sites-enabled/${DOMAIN}.conf"
nginx -t && systemctl reload nginx

echo "==> Проверка callback"
curl -sS "https://${DOMAIN}/avito/oauth/callback" | head -3

echo "OK. Redirect URL для Авито: https://${DOMAIN}/avito/oauth/callback"
