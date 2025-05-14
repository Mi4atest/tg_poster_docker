#!/bin/bash

# Скрипт для восстановления из резервной копии

# Проверка наличия аргументов
if [ $# -lt 2 ]; then
    echo "Использование: $0 <путь_к_резервной_копии_базы_данных> <путь_к_резервной_копии_медиа> <пароль>"
    echo "Пример: $0 ./backups/backup_20230101_120000.sql.enc ./backups/media_20230101_120000.tar.gz.enc mypassword"
    exit 1
fi

DB_BACKUP_FILE="$1"
MEDIA_BACKUP_FILE="$2"
BACKUP_PASSWORD="$3"

# Проверка существования файлов резервных копий
if [ ! -f "$DB_BACKUP_FILE" ]; then
    echo "Файл резервной копии базы данных не найден: $DB_BACKUP_FILE"
    exit 1
fi

if [ ! -f "$MEDIA_BACKUP_FILE" ]; then
    echo "Файл резервной копии медиа-файлов не найден: $MEDIA_BACKUP_FILE"
    exit 1
fi

# Временные файлы для расшифрованных резервных копий
TEMP_DB_BACKUP_FILE="${DB_BACKUP_FILE%.enc}"
TEMP_MEDIA_BACKUP_FILE="${MEDIA_BACKUP_FILE%.enc}"

echo "Расшифровка резервной копии базы данных..."
openssl enc -d -aes-256-cbc -in "$DB_BACKUP_FILE" -out "$TEMP_DB_BACKUP_FILE" -k "$BACKUP_PASSWORD"

# Проверка успешности расшифровки
if [ $? -ne 0 ]; then
    echo "Ошибка при расшифровке резервной копии базы данных!"
    exit 1
fi

echo "Расшифровка резервной копии медиа-файлов..."
openssl enc -d -aes-256-cbc -in "$MEDIA_BACKUP_FILE" -out "$TEMP_MEDIA_BACKUP_FILE" -k "$BACKUP_PASSWORD"

# Проверка успешности расшифровки
if [ $? -ne 0 ]; then
    echo "Ошибка при расшифровке резервной копии медиа-файлов!"
    rm -f "$TEMP_DB_BACKUP_FILE"
    exit 1
fi

echo "Восстановление базы данных..."
cat "$TEMP_DB_BACKUP_FILE" | docker-compose exec -T db psql -U postgres -d tg_poster

# Проверка успешности восстановления
if [ $? -ne 0 ]; then
    echo "Ошибка при восстановлении базы данных!"
    rm -f "$TEMP_DB_BACKUP_FILE" "$TEMP_MEDIA_BACKUP_FILE"
    exit 1
fi

echo "Восстановление медиа-файлов..."
rm -rf ./media/*
tar -xzf "$TEMP_MEDIA_BACKUP_FILE" -C ./media

# Проверка успешности восстановления
if [ $? -ne 0 ]; then
    echo "Ошибка при восстановлении медиа-файлов!"
    rm -f "$TEMP_DB_BACKUP_FILE" "$TEMP_MEDIA_BACKUP_FILE"
    exit 1
fi

# Удаление временных файлов
rm -f "$TEMP_DB_BACKUP_FILE" "$TEMP_MEDIA_BACKUP_FILE"

echo "Восстановление из резервной копии завершено успешно!"
