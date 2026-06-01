#!/bin/bash
# Запустить после того, как DNS: appleshop.ap43.ru → 94.159.110.11
set -euo pipefail
DOMAIN="appleshop.ap43.ru"
PROJECT_DIR="${PROJECT_DIR:-/root/tg_poster_docker}"

echo "DNS check:"
dig +short "$DOMAIN" @8.8.8.8
IP=$(curl -sS ifconfig.me 2>/dev/null || true)
echo "This server public IP: $IP"
echo "Expected: $IP in DNS for $DOMAIN"
echo

certbot certonly --webroot -w /var/www/certbot -d "$DOMAIN" \
  --agree-tos --register-unsafely-without-email --non-interactive

cp -f "$PROJECT_DIR/nginx/host-appleshop.ap43.ru.conf.example" \
  "/etc/nginx/sites-available/${DOMAIN}.conf"
nginx -t && systemctl reload nginx

curl -sS "https://${DOMAIN}/avito/oauth/callback" | head -3
echo
echo "Done: https://${DOMAIN}/avito/oauth/callback"
