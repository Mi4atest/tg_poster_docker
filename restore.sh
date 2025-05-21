#!/bin/bash

# Скрипт для восстановления базы данных из резервной копии

# Загружаем переменные окружения из .env файла
if [ -f "$(dirname "$0")/.env" ]; then
    source "$(dirname "$0")/.env"
fi

# Проверяем наличие имени проекта
if [ -z "$BACKUP_PROJECT_NAME" ]; then
    BACKUP_PROJECT_NAME="tg_poster"
    echo "Имя проекта не задано, используется значение по умолчанию: $BACKUP_PROJECT_NAME"
fi

# Если аргумент не передан, используем последнюю резервную копию
if [ -z "$1" ]; then
    echo "Путь к файлу резервной копии не указан, ищем последнюю резервную копию..."
    BACKUP_FILE=$(find "$(dirname "$0")/backups" -name "${BACKUP_PROJECT_NAME}_backup_*.gz" -type f -printf "%T@ %p\n" | sort -nr | head -n 1 | cut -d' ' -f2-)
    
    if [ -z "$BACKUP_FILE" ]; then
        echo "Ошибка: Резервные копии не найдены в директории backups/"
        echo "Использование: $0 <путь_к_файлу_резервной_копии>"
        exit 1
    fi
    
    echo "Найдена последняя резервная копия: $BACKUP_FILE"
else
    BACKUP_FILE="$1"
fi

# Проверяем существование файла
if [ ! -f "$BACKUP_FILE" ]; then
    echo "Ошибка: Файл '$BACKUP_FILE' не существует"
    exit 1
fi

# Проверяем, является ли файл сжатым
if [[ "$BACKUP_FILE" == *.gz ]]; then
    echo "Обнаружен сжатый файл. Распаковка..."
    TEMP_FILE="${BACKUP_FILE%.gz}"
    gunzip -c "$BACKUP_FILE" > "$TEMP_FILE"
    BACKUP_FILE="$TEMP_FILE"
    echo "Файл распакован: $BACKUP_FILE"
fi

# Запрашиваем подтверждение
echo "ВНИМАНИЕ: Это действие перезапишет текущую базу данных!"
echo "Вы уверены, что хотите восстановить базу данных из файла '$BACKUP_FILE'? (y/n)"
read -r confirm

if [[ "$confirm" != "y" && "$confirm" != "Y" ]]; then
    echo "Восстановление отменено."
    # Удаляем временный распакованный файл, если он был создан
    if [[ "$TEMP_FILE" && -f "$TEMP_FILE" ]]; then
        rm "$TEMP_FILE"
    fi
    exit 0
fi

echo "Начинаем восстановление базы данных..."

# Восстанавливаем базу данных
cat "$BACKUP_FILE" | docker-compose exec -T db psql -U postgres -d tg_poster

# Проверяем результат
if [ $? -eq 0 ]; then
    echo "✅ Восстановление базы данных успешно завершено!"
else
    echo "❌ Ошибка при восстановлении базы данных."
fi

# Удаляем временный распакованный файл, если он был создан
if [[ "$TEMP_FILE" && -f "$TEMP_FILE" ]]; then
    rm "$TEMP_FILE"
    echo "Временный файл удален."
fi

exit 0
