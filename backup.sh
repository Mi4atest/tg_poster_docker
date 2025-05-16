#!/bin/bash

# Настройки
BACKUP_DIR="$(dirname "$0")/backups"
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
BACKUP_FILE="tg_poster_backup_$TIMESTAMP.sql"
COMPRESSED_FILE="$BACKUP_FILE.gz"
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

# Функция для логирования
log() {
    echo "[$(date +"%Y-%m-%d %H:%M:%S")] $1" | tee -a "$LOG_FILE"
}

# Функция для отправки сообщений в Telegram
send_message() {
    curl -s -X POST "https://api.telegram.org/bot$BACKUP_BOT_TOKEN/sendMessage" \
         -d "chat_id=$BACKUP_CHAT_ID" \
         -d "text=$1" \
         -d "parse_mode=HTML"
}

# Функция для отправки файлов в Telegram
send_file() {
    curl -s -F document=@"$1" \
         -F caption="$2" \
         "https://api.telegram.org/bot$BACKUP_BOT_TOKEN/sendDocument?chat_id=$BACKUP_CHAT_ID"
}

# Создаем директорию для бэкапов, если она не существует
mkdir -p "$BACKUP_DIR"
touch "$LOG_FILE"

# Отправляем уведомление о начале резервного копирования
log "Начало процесса резервного копирования"
send_message "🔄 <b>Начато резервное копирование базы данных</b>"

# Создаем резервную копию базы данных
log "Создание резервной копии базы данных..."
docker-compose -f "$(dirname "$0")/docker-compose.yml" exec -T db pg_dump -U postgres tg_poster > "$BACKUP_DIR/$BACKUP_FILE"

# Проверяем, успешно ли создан файл
if [ ! -s "$BACKUP_DIR/$BACKUP_FILE" ]; then
    log "Ошибка: Файл резервной копии пуст или не создан"
    send_message "❌ <b>Ошибка: Файл резервной копии не создан!</b>"
    exit 1
fi

# Проверяем размер файла
if [ $(stat -c%s "$BACKUP_DIR/$BACKUP_FILE") -lt 1000 ]; then
    log "Ошибка: Файл резервной копии слишком мал ($(stat -c%s "$BACKUP_DIR/$BACKUP_FILE") байт)"
    send_message "❌ <b>Ошибка: Файл резервной копии слишком мал!</b>"
    exit 1
fi

# Сжимаем файл
log "Сжатие файла резервной копии..."
gzip -f "$BACKUP_DIR/$BACKUP_FILE"

# Проверяем, успешно ли сжат файл
if [ ! -f "$BACKUP_DIR/$COMPRESSED_FILE" ]; then
    log "Ошибка: Не удалось сжать файл резервной копии"
    send_message "❌ <b>Ошибка: Не удалось сжать файл резервной копии!</b>"
    exit 1
fi

# Отправляем файл в Telegram
log "Отправка файла в Telegram..."
FILESIZE=$(du -h "$BACKUP_DIR/$COMPRESSED_FILE" | cut -f1)
send_file "$BACKUP_DIR/$COMPRESSED_FILE" "Резервная копия базы данных tg_poster от $(date +"%d.%m.%Y %H:%M"). Размер: $FILESIZE"

# Удаляем старые резервные копии (оставляем последние 7)
log "Удаление старых резервных копий..."
find "$BACKUP_DIR" -name "tg_poster_backup_*.gz" -type f -mtime +30 -delete
log "Удалены файлы старше 30 дней"

# Если общий размер бэкапов превышает 1GB, удаляем самые старые
while [ $(du -sm "$BACKUP_DIR" | cut -f1) -gt 1000 ]; do
    oldest_file=$(ls -tr "$BACKUP_DIR"/tg_poster_backup_*.gz 2>/dev/null | head -n 1)
    if [ -z "$oldest_file" ]; then
        break
    fi
    rm -f "$oldest_file"
    log "Удален старый файл: $oldest_file"
done

# Отправляем уведомление о завершении резервного копирования
log "Резервное копирование завершено успешно!"
send_message "✅ <b>Резервное копирование завершено успешно!</b>
📁 Имя файла: $COMPRESSED_FILE
📊 Размер: $FILESIZE
🕒 Дата: $(date +"%d.%m.%Y %H:%M")"

exit 0
