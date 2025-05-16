#!/bin/bash

# Скрипт для настройки автоматического резервного копирования

# Определяем путь к директории проекта
PROJECT_DIR="$(dirname "$(readlink -f "$0")")"
BACKUP_SCRIPT="$PROJECT_DIR/backup.sh"

# Проверяем наличие скрипта резервного копирования
if [ ! -f "$BACKUP_SCRIPT" ]; then
    echo "Ошибка: Скрипт резервного копирования не найден: $BACKUP_SCRIPT"
    exit 1
fi

# Делаем скрипты исполняемыми
chmod +x "$BACKUP_SCRIPT"
chmod +x "$PROJECT_DIR/restore.sh"

# Проверяем наличие переменных окружения для Telegram в .env
ENV_FILE="$PROJECT_DIR/.env"
if [ ! -f "$ENV_FILE" ]; then
    echo "Ошибка: Файл .env не найден"
    exit 1
fi

# Проверяем, есть ли уже переменные BACKUP_BOT_TOKEN и BACKUP_CHAT_ID в .env
if ! grep -q "BACKUP_BOT_TOKEN" "$ENV_FILE" || ! grep -q "BACKUP_CHAT_ID" "$ENV_FILE"; then
    echo "Необходимо добавить переменные для резервного копирования в файл .env"
    
    # Запрашиваем токен бота
    echo -n "Введите токен Telegram-бота (получите его у @BotFather): "
    read -r bot_token
    
    # Запрашиваем ID чата
    echo -n "Введите ID чата для отправки резервных копий (получите его у @userinfobot): "
    read -r chat_id
    
    # Добавляем переменные в .env
    echo "" >> "$ENV_FILE"
    echo "# Настройки резервного копирования" >> "$ENV_FILE"
    echo "BACKUP_BOT_TOKEN=$bot_token" >> "$ENV_FILE"
    echo "BACKUP_CHAT_ID=$chat_id" >> "$ENV_FILE"
    
    echo "Переменные добавлены в файл .env"
fi

# Создаем директорию для резервных копий
mkdir -p "$PROJECT_DIR/backups"

# Настраиваем cron задание
echo "Настройка автоматического резервного копирования через cron"
echo "По умолчанию резервное копирование будет выполняться ежедневно в 3:00 утра"
echo -n "Хотите изменить время выполнения? (y/n): "
read -r change_time

if [[ "$change_time" == "y" || "$change_time" == "Y" ]]; then
    echo -n "Введите час (0-23): "
    read -r hour
    echo -n "Введите минуту (0-59): "
    read -r minute
else
    hour=3
    minute=0
fi

# Создаем временный файл для crontab
TEMP_CRON=$(mktemp)
crontab -l > "$TEMP_CRON" 2>/dev/null || true

# Проверяем, есть ли уже задание для backup.sh
if grep -q "$BACKUP_SCRIPT" "$TEMP_CRON"; then
    echo "Задание для резервного копирования уже существует в crontab"
    echo "Текущее задание:"
    grep "$BACKUP_SCRIPT" "$TEMP_CRON"
    
    echo -n "Хотите обновить существующее задание? (y/n): "
    read -r update_cron
    
    if [[ "$update_cron" == "y" || "$update_cron" == "Y" ]]; then
        # Удаляем существующее задание
        sed -i "\|$BACKUP_SCRIPT|d" "$TEMP_CRON"
        # Добавляем новое задание
        echo "$minute $hour * * * $BACKUP_SCRIPT >> $PROJECT_DIR/backups/backup.log 2>&1" >> "$TEMP_CRON"
        crontab "$TEMP_CRON"
        echo "Задание обновлено"
    fi
else
    # Добавляем новое задание
    echo "$minute $hour * * * $BACKUP_SCRIPT >> $PROJECT_DIR/backups/backup.log 2>&1" >> "$TEMP_CRON"
    crontab "$TEMP_CRON"
    echo "Задание добавлено в crontab"
fi

# Удаляем временный файл
rm "$TEMP_CRON"

echo ""
echo "Настройка автоматического резервного копирования завершена!"
echo "Резервные копии будут создаваться ежедневно в $hour:$minute и отправляться в Telegram"
echo "Для ручного запуска резервного копирования выполните: $BACKUP_SCRIPT"
echo "Для восстановления из резервной копии выполните: $PROJECT_DIR/restore.sh <путь_к_файлу_резервной_копии>"
echo ""

# Предлагаем выполнить тестовое резервное копирование
echo -n "Хотите выполнить тестовое резервное копирование сейчас? (y/n): "
read -r test_backup

if [[ "$test_backup" == "y" || "$test_backup" == "Y" ]]; then
    echo "Запуск тестового резервного копирования..."
    "$BACKUP_SCRIPT"
fi

exit 0
