#!/bin/bash

# Скрипт для автоматического развертывания проекта TG Poster
# с настройками безопасности и отказоустойчивости

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

# Функция для проверки наличия команды
check_command() {
    if ! command -v $1 &> /dev/null; then
        log_error "Команда $1 не найдена. Установка..."
        return 1
    fi
    return 0
}

# Функция для установки зависимостей
install_dependencies() {
    log "Установка зависимостей..."
    
    # Обновление списка пакетов
    sudo apt-get update
    
    # Установка необходимых пакетов
    sudo apt-get install -y \
        apt-transport-https \
        ca-certificates \
        curl \
        gnupg \
        lsb-release \
        ufw \
        openssl
    
    # Установка Docker, если он не установлен
    if ! check_command docker; then
        log "Установка Docker..."
        curl -fsSL https://get.docker.com -o get-docker.sh
        sudo sh get-docker.sh
        sudo usermod -aG docker $USER
        log_success "Docker установлен"
    else
        log_success "Docker уже установлен"
    fi
    
    # Установка Docker Compose, если он не установлен
    if ! check_command docker-compose; then
        log "Установка Docker Compose..."
        sudo curl -L "https://github.com/docker/compose/releases/download/v2.18.1/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
        sudo chmod +x /usr/local/bin/docker-compose
        log_success "Docker Compose установлен"
    else
        log_success "Docker Compose уже установлен"
    fi
    
    log_success "Зависимости установлены"
}

# Функция для настройки файла .env
setup_env() {
    log "Настройка файла .env..."
    
    if [ ! -f .env ]; then
        if [ -f .env.example ]; then
            cp .env.example .env
            log "Файл .env создан из шаблона .env.example"
            
            # Генерация случайных паролей и ключей
            DB_PASSWORD=$(openssl rand -base64 12)
            SECRET_KEY=$(openssl rand -base64 32)
            WEBHOOK_SECRET=$(openssl rand -base64 24)
            
            # Обновление файла .env
            sed -i "s/DB_PASSWORD=.*/DB_PASSWORD=$DB_PASSWORD/" .env
            sed -i "s/SECRET_KEY=.*/SECRET_KEY=$SECRET_KEY/" .env
            sed -i "s/WEBHOOK_SECRET=.*/WEBHOOK_SECRET=$WEBHOOK_SECRET/" .env
            
            log_warning "Пожалуйста, отредактируйте файл .env и укажите свои токены и настройки"
            log_warning "Особенно важно указать TELEGRAM_BOT_TOKEN и ALLOWED_USER_IDS"
            
            # Открываем файл .env для редактирования
            if command -v nano &> /dev/null; then
                read -p "Хотите отредактировать файл .env сейчас? (y/n): " edit_env
                if [[ $edit_env == "y" || $edit_env == "Y" ]]; then
                    nano .env
                fi
            else
                log_warning "Редактор nano не установлен. Пожалуйста, отредактируйте файл .env вручную"
            fi
        else
            log_error "Файл .env.example не найден. Невозможно создать файл .env"
            exit 1
        fi
    else
        log_warning "Файл .env уже существует. Пропускаем создание"
    fi
    
    log_success "Файл .env настроен"
}

# Функция для генерации SSL-сертификатов
generate_ssl() {
    log "Генерация SSL-сертификатов..."
    
    # Создаем директорию для сертификатов, если она не существует
    mkdir -p nginx/ssl
    
    # Проверяем, существуют ли уже сертификаты
    if [ -f nginx/ssl/fullchain.pem ] && [ -f nginx/ssl/privkey.pem ]; then
        log_warning "SSL-сертификаты уже существуют. Пропускаем генерацию"
    else
        # Генерируем приватный ключ и сертификат
        openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
          -keyout nginx/ssl/privkey.pem \
          -out nginx/ssl/fullchain.pem \
          -subj "/C=RU/ST=State/L=City/O=Organization/CN=localhost"
        
        # Устанавливаем правильные разрешения
        chmod 600 nginx/ssl/privkey.pem
        chmod 644 nginx/ssl/fullchain.pem
        
        log_success "SSL-сертификаты успешно сгенерированы"
    fi
}

# Функция для настройки ModSecurity
setup_modsecurity() {
    log "Настройка ModSecurity..."
    
    # Создаем директорию для ModSecurity, если она не существует
    mkdir -p nginx/modsecurity
    
    # Проверяем, существует ли уже конфигурация ModSecurity
    if [ -f nginx/modsecurity/main.conf ]; then
        log_warning "Конфигурация ModSecurity уже существует. Пропускаем создание"
    else
        # Создаем конфигурационный файл ModSecurity
        cat > nginx/modsecurity/main.conf << 'EOF'
# Включение ModSecurity
SecRuleEngine On

# Базовые настройки
SecRequestBodyAccess On
SecResponseBodyAccess On
SecResponseBodyMimeType text/plain text/html text/xml application/json
SecResponseBodyLimit 1024

# Логирование
SecAuditEngine RelevantOnly
SecAuditLogRelevantStatus "^(?:5|4(?!04))"
SecAuditLogParts ABIJDEFHZ
SecAuditLogType Serial
SecAuditLog /var/log/nginx/modsec_audit.log

# Правила защиты от SQL-инъекций
SecRule REQUEST_COOKIES|REQUEST_COOKIES_NAMES|REQUEST_FILENAME|REQUEST_HEADERS|REQUEST_HEADERS_NAMES|REQUEST_METHOD|REQUEST_PROTOCOL|REQUEST_URI|REQUEST_URI_RAW|REQUEST_BODY|REQUEST_LINE "(?i:(?:select|create|rename|truncate|load|alter|delete|update|insert|desc).*(?:from|into|table))" \
    "id:1000,phase:2,deny,status:403,log,msg:'SQL Injection Attack'"

# Правила защиты от XSS
SecRule REQUEST_COOKIES|REQUEST_COOKIES_NAMES|REQUEST_HEADERS|REQUEST_HEADERS_NAMES|REQUEST_BODY|REQUEST_URI|REQUEST_URI_RAW|REQUEST_LINE "(?i:<script.*?>)" \
    "id:1001,phase:2,deny,status:403,log,msg:'XSS Attack'"

# Правила защиты от Path Traversal
SecRule REQUEST_URI|REQUEST_HEADERS|REQUEST_BODY "(?i:(?:\.\.|%2e%2e))" \
    "id:1002,phase:2,deny,status:403,log,msg:'Path Traversal Attack'"

# Правила защиты от Command Injection
SecRule REQUEST_COOKIES|REQUEST_HEADERS|REQUEST_BODY|REQUEST_URI "(?i:(?:;|\||`|&&|\$\(|\$\{))" \
    "id:1003,phase:2,deny,status:403,log,msg:'Command Injection Attack'"

# Правила защиты от File Inclusion
SecRule REQUEST_URI|REQUEST_BODY "(?i:(?:https?|ftp|php|data|file):)" \
    "id:1004,phase:2,deny,status:403,log,msg:'Remote File Inclusion Attack'"

# Правила защиты от Telegram API
# Разрешаем только запросы от Telegram к webhook
SecRule REMOTE_ADDR "!@ipMatch 149.154.160.0/20,91.108.4.0/22" \
    "chain,id:1005,phase:1,deny,status:403,log,msg:'Access denied from non-Telegram IP'"
SecRule REQUEST_URI "^/api/telegram/webhook" \
    "t:none"
EOF
        
        log_success "Конфигурация ModSecurity успешно создана"
    fi
}

# Функция для настройки брандмауэра
setup_firewall() {
    log "Настройка брандмауэра (UFW)..."
    
    # Проверка наличия UFW
    if ! check_command ufw; then
        sudo apt-get install -y ufw
    fi
    
    # Сброс правил UFW
    sudo ufw --force reset
    
    # Установка политики по умолчанию
    sudo ufw default deny incoming
    sudo ufw default allow outgoing
    
    # Разрешение SSH (для удаленного доступа)
    sudo ufw allow ssh
    
    # Разрешение HTTP и HTTPS
    sudo ufw allow 80/tcp
    sudo ufw allow 443/tcp
    
    # Разрешение доступа только с IP-адресов Telegram для webhook
    for ip in 149.154.160.0/20 91.108.4.0/22; do
        sudo ufw allow from $ip to any port 443 proto tcp
    done
    
    # Блокировка всех остальных портов
    sudo ufw deny 8002/tcp  # API
    sudo ufw deny 5432/tcp  # PostgreSQL
    sudo ufw deny 8080/tcp  # Nginx (старый порт)
    
    # Включение UFW
    sudo ufw --force enable
    
    log_success "Брандмауэр настроен"
    sudo ufw status verbose
}

# Функция для запуска контейнеров
start_containers() {
    log "Запуск контейнеров..."
    
    # Остановка контейнеров, если они уже запущены
    if [ -f docker-compose.yml ]; then
        docker-compose down
    fi
    
    # Запуск контейнеров
    docker-compose up -d
    
    log_success "Контейнеры запущены"
}

# Функция для инициализации базы данных
init_database() {
    log "Инициализация базы данных..."
    
    # Ждем, пока база данных запустится
    sleep 10
    
    # Инициализация базы данных
    docker-compose exec -T app python -c "from app.db.database import Base, engine; from app.api.models.post import Post, PublicationLog; from app.api.models.story import Story, StoryPublicationLog; Base.metadata.create_all(bind=engine)"
    
    log_success "База данных инициализирована"
}

# Функция для создания директории для резервных копий
setup_backups() {
    log "Настройка резервного копирования..."
    
    # Создание директории для резервных копий
    mkdir -p backups
    
    # Создание cron-задания для ежедневного резервного копирования
    (crontab -l 2>/dev/null; echo "0 2 * * * cd $(pwd) && ./backup.sh") | crontab -
    
    log_success "Резервное копирование настроено"
}

# Основная функция
main() {
    log "Начало развертывания проекта TG Poster..."
    
    # Установка зависимостей
    install_dependencies
    
    # Настройка файла .env
    setup_env
    
    # Генерация SSL-сертификатов
    generate_ssl
    
    # Настройка ModSecurity
    setup_modsecurity
    
    # Настройка брандмауэра
    setup_firewall
    
    # Запуск контейнеров
    start_containers
    
    # Инициализация базы данных
    init_database
    
    # Настройка резервного копирования
    setup_backups
    
    log_success "Проект TG Poster успешно развернут!"
    log "API доступен по адресу: https://ваш-домен"
    log "Для доступа к боту используйте Telegram"
}

# Запуск основной функции
main
