#!/bin/bash

# Настройки
BACKUP_DIR="$(dirname "$0")/backups"
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
LOG_FILE="$BACKUP_DIR/backup.log"

# Загружаем переменные окружения из .env файла
if [ -f "$(dirname "$0")/.env" ]; then
    source "$(dirname "$0")/.env"
fi

# Проверяем наличие переменных окружения для Telegram
if [ -z "$BACKUP_BOT_TOKEN" ] || [ -z "$BACKUP_CHAT_ID" ]; then
    echo "Ошибка: Не заданы переменные окружения BACKUP_BOT_TOKEN и/или BACKUP_CHAT_ID"
    echo "Добавьте их в файл .env или задайте вручную в скрипте"
    exit 1
fi

# Проверяем наличие имени проекта
if [ -z "$BACKUP_PROJECT_NAME" ]; then
    BACKUP_PROJECT_NAME="tg_poster"
    echo "Имя проекта не задано, используется значение по умолчанию: $BACKUP_PROJECT_NAME"
fi

# Устанавливаем имя файла резервной копии с использованием имени проекта
BACKUP_FILE="${BACKUP_PROJECT_NAME}_backup_$TIMESTAMP.sql"
COMPRESSED_FILE="$BACKUP_FILE.gz"

# Функция для логирования
log() {
    echo "[$(date +"%Y-%m-%d %H:%M:%S")] $1" | tee -a "$LOG_FILE"
}

# Функция для отправки файлов в Telegram с расширенным описанием
send_file() {
    local file="$1"
    local server_name=$(hostname)
    local server_ip=$(hostname -I | awk '{print $1}')
    
    local caption="📦 <b>Резервная копия базы данных</b>
🔹 <b>Проект:</b> ${BACKUP_PROJECT_NAME}
🔹 <b>Сервер:</b> ${server_name} (${server_ip})
🔹 <b>Дата:</b> $(date +"%d.%m.%Y %H:%M")
🔹 <b>Размер:</b> $(du -h "$file" | cut -f1)"

    curl -s -F document=@"$file" \
         -F caption="$caption" \
         -F parse_mode="HTML" \
         "https://api.telegram.org/bot$BACKUP_BOT_TOKEN/sendDocument?chat_id=$BACKUP_CHAT_ID"
}

# Создаем директорию для бэкапов, если она не существует
mkdir -p "$BACKUP_DIR"
touch "$LOG_FILE"

# Начинаем процесс резервного копирования
log "Начало процесса резервного копирования"

# Создаем резервную копию базы данных
log "Создание резервной копии базы данных..."
docker-compose -f "$(dirname "$0")/docker-compose.yml" exec -T db pg_dump -U postgres tg_poster > "$BACKUP_DIR/$BACKUP_FILE"

# Проверяем, успешно ли создан файл
if [ ! -s "$BACKUP_DIR/$BACKUP_FILE" ]; then
    log "Ошибка: Файл резервной копии пуст или не создан"
    exit 1
fi

# Проверяем размер файла
if [ $(stat -c%s "$BACKUP_DIR/$BACKUP_FILE") -lt 1000 ]; then
    log "Ошибка: Файл резервной копии слишком мал ($(stat -c%s "$BACKUP_DIR/$BACKUP_FILE") байт)"
    exit 1
fi

# Сжимаем файл
log "Сжатие файла резервной копии..."
gzip -f "$BACKUP_DIR/$BACKUP_FILE"

# Проверяем, успешно ли сжат файл
if [ ! -f "$BACKUP_DIR/$COMPRESSED_FILE" ]; then
    log "Ошибка: Не удалось сжать файл резервной копии"
    exit 1
fi

# Отправляем файл в Telegram
log "Отправка файла в Telegram..."
send_file "$BACKUP_DIR/$COMPRESSED_FILE"

# Добавляем резервное копирование медиа-файлов (опционально)
if [ "$BACKUP_MEDIA" = "true" ]; then
    log "Создание резервной копии медиа-файлов..."
    MEDIA_BACKUP_FILE="${BACKUP_PROJECT_NAME}_media_$TIMESTAMP.tar.gz"
    
    # Создаем архив медиа-файлов
    tar -czf "$BACKUP_DIR/$MEDIA_BACKUP_FILE" -C "$(dirname "$0")" media
    
    # Проверяем, успешно ли создан файл
    if [ ! -f "$BACKUP_DIR/$MEDIA_BACKUP_FILE" ]; then
        log "Ошибка: Файл резервной копии медиа не создан"
    else
        log "Резервная копия медиа-файлов создана: $MEDIA_BACKUP_FILE"
        
        # Отправляем файл в Telegram (если он не слишком большой)
        if [ $(stat -c%s "$BACKUP_DIR/$MEDIA_BACKUP_FILE") -lt 50000000 ]; then
            log "Отправка файла медиа-резервной копии в Telegram..."
            send_file "$BACKUP_DIR/$MEDIA_BACKUP_FILE"
        else
            log "Файл медиа-резервной копии слишком большой для отправки в Telegram"
        fi
    fi
fi

# Удаляем старые резервные копии (оставляем последние 7)
log "Удаление старых резервных копий..."
find "$BACKUP_DIR" -name "${BACKUP_PROJECT_NAME}_backup_*.gz" -type f -mtime +30 -delete
log "Удалены файлы старше 30 дней"

# Если общий размер бэкапов превышает 1GB, удаляем самые старые
while [ $(du -sm "$BACKUP_DIR" | cut -f1) -gt 1000 ]; do
    oldest_file=$(ls -tr "$BACKUP_DIR"/${BACKUP_PROJECT_NAME}_backup_*.gz 2>/dev/null | head -n 1)
    if [ -z "$oldest_file" ]; then
        break
    fi
    rm -f "$oldest_file"
    log "Удален старый файл: $oldest_file"
done

log "Резервное копирование завершено успешно!"
exit 0
