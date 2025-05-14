# Совместимость с AntiZapret-VPN

Этот документ описывает настройки для совместного использования TG Poster и AntiZapret-VPN на одном сервере.

## Изменения для обеспечения совместимости

Для обеспечения совместимости с AntiZapret-VPN были внесены следующие изменения:

### 1. Использование нестандартных портов

TG Poster настроен на использование следующих портов:
- HTTP: 8080 (вместо 80)
- HTTPS: 8443 (вместо 443)

Это позволяет избежать конфликтов с AntiZapret-VPN, который может использовать стандартные порты 80 и 443.

### 2. Использование отдельного IP-диапазона для Docker

TG Poster настроен на использование следующего IP-диапазона для Docker:
- Сеть Docker: 192.168.100.0/24
- Шлюз: 192.168.100.1

Это позволяет избежать конфликтов с IP-диапазонами, используемыми AntiZapret-VPN:
- Клиенты обычного VPN: 10.28.0.0/22, 10.28.4.0/22, 10.28.8.0/24 (или 172.28.0.0/22, 172.28.4.0/22, 172.28.8.0/24)
- Клиенты AntiZapret VPN: 10.29.0.0/22, 10.29.4.0/22, 10.29.8.0/24 (или 172.29.0.0/22, 172.29.4.0/22, 172.29.8.0/24)
- DNS АнтиЗапрета: 10.29.0.1, 10.29.4.1, 10.29.8.1 (или 172.29.0.1, 172.29.4.1, 172.29.8.1)
- Подменные IP АнтиЗапрета: 10.30.0.0/15 (или 172.30.0.0/15)

### 3. Использование iptables вместо UFW

TG Poster автоматически определяет наличие AntiZapret-VPN на сервере и:
- Если AntiZapret-VPN установлен, использует iptables для настройки брандмауэра
- Если AntiZapret-VPN не установлен, использует UFW для настройки брандмауэра

При использовании iptables:
- Создаются отдельные цепочки для TG Poster (TG_POSTER_INPUT, TG_POSTER_FORWARD)
- Правила добавляются только в эти цепочки, не затрагивая основные цепочки
- Это позволяет избежать конфликтов с правилами iptables, настроенными AntiZapret-VPN

## Порядок установки

Рекомендуемый порядок установки:

1. Сначала установите AntiZapret-VPN:
   ```bash
   bash <(wget --no-hsts -qO- https://raw.githubusercontent.com/GubernievS/AntiZapret-VPN/main/setup.sh)
   ```

2. Затем установите TG Poster:
   ```bash
   git clone https://github.com/Mi4atest/tg_poster_docker.git
   cd tg_poster_docker
   git checkout security-improvements
   ./deploy.sh
   ```

## Проверка совместимости

После установки обоих проектов рекомендуется проверить:

1. Доступность TG Poster по нестандартным портам:
   ```bash
   curl -k https://ваш-домен:8443
   ```

2. Доступность AntiZapret-VPN:
   ```bash
   # Проверка OpenVPN
   systemctl status openvpn-server@server-udp-1194
   
   # Проверка WireGuard
   systemctl status wg-quick@wg0
   ```

3. Отсутствие конфликтов в правилах iptables:
   ```bash
   iptables -L -v
   ```

## Устранение неполадок

### Проблема: TG Poster недоступен по нестандартным портам

Проверьте, что порты 8080 и 8443 открыты в iptables:
```bash
iptables -L TG_POSTER_INPUT -v
```

Убедитесь, что Nginx настроен на прослушивание этих портов:
```bash
docker-compose exec nginx nginx -T
```

### Проблема: AntiZapret-VPN не работает после установки TG Poster

Проверьте, что правила iptables для AntiZapret-VPN не были изменены:
```bash
iptables -L
```

Перезапустите службы AntiZapret-VPN:
```bash
systemctl restart openvpn-server@server-udp-1194
systemctl restart wg-quick@wg0
```

### Проблема: Конфликты IP-диапазонов

Проверьте, что IP-диапазоны не пересекаются:
```bash
ip route
```

Если обнаружены конфликты, измените IP-диапазон для Docker в файле docker-compose.yml.
