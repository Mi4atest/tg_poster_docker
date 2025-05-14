#!/bin/bash

# Скрипт для автоматического резервного копирования

# Директория для резервных копий
BACKUP_DIR="./backups"
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
BACKUP_FILE="${BACKUP_DIR}/backup_${TIMESTAMP}.sql"
ENCRYPTED_BACKUP_FILE="${BACKUP_FILE}.enc"

# Создание директории для резервных копий, если она не существует
mkdir -p "$BACKUP_DIR"

# Проверка наличия пароля для шифрования
if [ -z "$BACKUP_PASSWORD" ]; then
    # Если пароль не задан, генерируем случайный
    BACKUP_PASSWORD=$(openssl rand -base64 32)
    echo "Сгенерирован случайный пароль для шифрования: $BACKUP_PASSWORD"
    echo "Сохраните этот пароль в надежном месте для восстановления резервной копии!"
fi

echo "Создание резервной копии базы данных..."

# Создание резервной копии базы данных
docker-compose exec -T db pg_dump -U postgres tg_poster > "$BACKUP_FILE"

# Проверка успешности создания резервной копии
if [ $? -ne 0 ]; then
    echo "Ошибка при создании резервной копии базы данных!"
    exit 1
fi

echo "Резервная копия базы данных создана: $BACKUP_FILE"

# Шифрование резервной копии
echo "Шифрование резервной копии..."
openssl enc -aes-256-cbc -salt -in "$BACKUP_FILE" -out "$ENCRYPTED_BACKUP_FILE" -k "$BACKUP_PASSWORD"

# Проверка успешности шифрования
if [ $? -ne 0 ]; then
    echo "Ошибка при шифровании резервной копии!"
    exit 1
fi

# Удаление нешифрованной резервной копии
rm "$BACKUP_FILE"

echo "Резервная копия зашифрована: $ENCRYPTED_BACKUP_FILE"

# Создание резервной копии медиа-файлов
MEDIA_BACKUP_FILE="${BACKUP_DIR}/media_${TIMESTAMP}.tar.gz"
ENCRYPTED_MEDIA_BACKUP_FILE="${MEDIA_BACKUP_FILE}.enc"

echo "Создание резервной копии медиа-файлов..."
tar -czf "$MEDIA_BACKUP_FILE" -C ./media .

# Проверка успешности создания резервной копии
if [ $? -ne 0 ]; then
    echo "Ошибка при создании резервной копии медиа-файлов!"
    exit 1
fi

echo "Резервная копия медиа-файлов создана: $MEDIA_BACKUP_FILE"

# Шифрование резервной копии медиа-файлов
echo "Шифрование резервной копии медиа-файлов..."
openssl enc -aes-256-cbc -salt -in "$MEDIA_BACKUP_FILE" -out "$ENCRYPTED_MEDIA_BACKUP_FILE" -k "$BACKUP_PASSWORD"

# Проверка успешности шифрования
if [ $? -ne 0 ]; then
    echo "Ошибка при шифровании резервной копии медиа-файлов!"
    exit 1
fi

# Удаление нешифрованной резервной копии
rm "$MEDIA_BACKUP_FILE"

echo "Резервная копия медиа-файлов зашифрована: $ENCRYPTED_MEDIA_BACKUP_FILE"

# Удаление старых резервных копий (оставляем последние 7)
echo "Удаление старых резервных копий..."
ls -t "${BACKUP_DIR}"/*.sql.enc | tail -n +8 | xargs -r rm
ls -t "${BACKUP_DIR}"/*.tar.gz.enc | tail -n +8 | xargs -r rm

echo "Резервное копирование завершено успешно!"
