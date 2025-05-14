#!/bin/bash

# Скрипт для настройки iptables без использования UFW
# Этот скрипт добавляет правила iptables для TG Poster, не затрагивая правила AntiZapret-VPN

# Цвета для вывода
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Функция для вывода сообщений
log() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Проверка наличия iptables
if ! command -v iptables &> /dev/null; then
    log_error "iptables не установлен. Установка..."
    apt-get update
    apt-get install -y iptables
fi

# Создаем цепочки для TG Poster
log "Создание цепочек для TG Poster..."

# Проверяем, существует ли цепочка TG_POSTER_INPUT
iptables -L TG_POSTER_INPUT &> /dev/null
if [ $? -ne 0 ]; then
    iptables -N TG_POSTER_INPUT
    log_success "Создана цепочка TG_POSTER_INPUT"
else
    log_warning "Цепочка TG_POSTER_INPUT уже существует"
fi

# Проверяем, существует ли цепочка TG_POSTER_FORWARD
iptables -L TG_POSTER_FORWARD &> /dev/null
if [ $? -ne 0 ]; then
    iptables -N TG_POSTER_FORWARD
    log_success "Создана цепочка TG_POSTER_FORWARD"
else
    log_warning "Цепочка TG_POSTER_FORWARD уже существует"
fi

# Очищаем цепочки
iptables -F TG_POSTER_INPUT
iptables -F TG_POSTER_FORWARD

# Добавляем правила в цепочку TG_POSTER_INPUT
log "Добавление правил в цепочку TG_POSTER_INPUT..."

# Разрешаем SSH
iptables -A TG_POSTER_INPUT -p tcp --dport 22 -j ACCEPT

# Разрешаем HTTP и HTTPS на нестандартных портах
iptables -A TG_POSTER_INPUT -p tcp --dport 8080 -j ACCEPT
iptables -A TG_POSTER_INPUT -p tcp --dport 8443 -j ACCEPT

# Разрешаем доступ только с IP-адресов Telegram для webhook
for ip in 149.154.160.0/20 91.108.4.0/22; do
    iptables -A TG_POSTER_INPUT -p tcp --dport 8443 -s $ip -j ACCEPT
done

# Разрешаем доступ к локальной сети Docker
iptables -A TG_POSTER_INPUT -s 192.168.100.0/24 -j ACCEPT

# Разрешаем доступ к localhost
iptables -A TG_POSTER_INPUT -i lo -j ACCEPT

# Разрешаем установленные соединения
iptables -A TG_POSTER_INPUT -m state --state ESTABLISHED,RELATED -j ACCEPT

# Добавляем правила в цепочку TG_POSTER_FORWARD
log "Добавление правил в цепочку TG_POSTER_FORWARD..."

# Разрешаем форвардинг для Docker
iptables -A TG_POSTER_FORWARD -s 192.168.100.0/24 -j ACCEPT
iptables -A TG_POSTER_FORWARD -d 192.168.100.0/24 -j ACCEPT

# Проверяем, есть ли ссылка на нашу цепочку в INPUT
iptables -C INPUT -j TG_POSTER_INPUT &> /dev/null
if [ $? -ne 0 ]; then
    # Добавляем ссылку на нашу цепочку в INPUT (в начало)
    iptables -I INPUT 1 -j TG_POSTER_INPUT
    log_success "Добавлена ссылка на TG_POSTER_INPUT в INPUT"
else
    log_warning "Ссылка на TG_POSTER_INPUT в INPUT уже существует"
fi

# Проверяем, есть ли ссылка на нашу цепочку в FORWARD
iptables -C FORWARD -j TG_POSTER_FORWARD &> /dev/null
if [ $? -ne 0 ]; then
    # Добавляем ссылку на нашу цепочку в FORWARD (в начало)
    iptables -I FORWARD 1 -j TG_POSTER_FORWARD
    log_success "Добавлена ссылка на TG_POSTER_FORWARD в FORWARD"
else
    log_warning "Ссылка на TG_POSTER_FORWARD в FORWARD уже существует"
fi

# Настройка NAT для Docker
log "Настройка NAT для Docker..."
iptables -t nat -A POSTROUTING -s 192.168.100.0/24 ! -o docker0 -j MASQUERADE

# Сохранение правил iptables
log "Сохранение правил iptables..."
if command -v iptables-save &> /dev/null; then
    if [ -d /etc/iptables ]; then
        iptables-save > /etc/iptables/rules.v4
        log_success "Правила iptables сохранены в /etc/iptables/rules.v4"
    else
        mkdir -p /etc/iptables
        iptables-save > /etc/iptables/rules.v4
        log_success "Создана директория /etc/iptables и правила сохранены"
    fi
else
    log_warning "iptables-save не найден, правила не сохранены"
fi

# Настройка автоматического восстановления правил при перезагрузке
log "Настройка автоматического восстановления правил при перезагрузке..."
if [ -f /etc/systemd/system/iptables-restore.service ]; then
    log_warning "Сервис iptables-restore.service уже существует"
else
    cat > /etc/systemd/system/iptables-restore.service << EOF
[Unit]
Description=Restore iptables rules
Before=network.target

[Service]
Type=oneshot
ExecStart=/sbin/iptables-restore /etc/iptables/rules.v4
ExecStartPost=/sbin/ip6tables-restore /etc/iptables/rules.v6 || true

[Install]
WantedBy=multi-user.target
EOF
    systemctl daemon-reload
    systemctl enable iptables-restore.service
    log_success "Создан и включен сервис iptables-restore.service"
fi

log_success "Настройка iptables завершена"
log "Текущие правила iptables:"
iptables -L -v
