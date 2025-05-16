#!/bin/bash

# Скрипт для восстановления базы данных из резервной копии

# Проверяем, передан ли файл резервной копии
if [ -z "$1" ]; then
    echo "Ошибка: Не указан файл резервной копии"
    echo "Использование: $0 <путь_к_файлу_резервной_копии>"
    exit 1
fi

BACKUP_FILE="$1"

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
