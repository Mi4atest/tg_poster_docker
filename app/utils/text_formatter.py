import re
from typing import Optional
from app.utils.signatures import get_telegram_signature, get_vk_signature, get_instagram_signature

def format_post_text(text: str) -> str:
    """
    Форматирует текст поста для более привлекательного отображения.
    
    Правила форматирования:
    - Выделяет жирным первую строку (название модели)
    - Выделяет жирным цену
    - Выделяет курсивом блоки между разделителями "————————"
    - Выделяет жирным ключевые параметры (Комплект, Аккумулятор, IMEI и т.д.)
    - Обеспечивает правильные переносы строк
    
    Args:
        text: Исходный текст поста
        
    Returns:
        str: Отформатированный текст
    """
    if not text:
        return ""
    
    # Разбиваем текст на строки
    lines = text.strip().split('\n')
    formatted_lines = []
    
    # Флаг для определения, находимся ли мы в блоке между разделителями
    in_special_block = False
    
    # Обрабатываем каждую строку
    for i, line in enumerate(lines):
        line = line.strip()
        
        # Пропускаем пустые строки
        if not line:
            formatted_lines.append("")
            continue
        
        # Проверяем, является ли строка разделителем
        if re.match(r'^-{5,}$', line.replace('—', '-')):
            in_special_block = not in_special_block
            formatted_lines.append("—" * 30)
            continue
        
        # Форматируем первую непустую строку (название модели) жирным
        if i == 0 or (i > 0 and not any(lines[:i])):
            # Удаляем лишние эмодзи из названия для форматирования
            model_name = re.sub(r'(🔥|👍|⭐️|📱|📲|💯|🎁|🎄|🎀)+', '', line)
            model_name = model_name.strip()
            
            # Добавляем эмодзи обратно, если они были
            if '🔥' in line:
                formatted_lines.append(f"🔥 *{model_name}* 🔥")
            else:
                formatted_lines.append(f"*{model_name}*")
            continue
        
        # Форматируем строку с ценой
        if '💵' in line or 'Цена:' in line.lower() or 'цена' in line.lower():
            # Извлекаем цену
            price_match = re.search(r'(\d+[\s\.,]?\d*)\s*(?:руб|р|₽|RUB)', line, re.IGNORECASE)
            if price_match:
                price = price_match.group(1).replace(' ', '')
                # Форматируем цену с пробелами между тысячами
                try:
                    price_int = int(price.replace(',', '').replace('.', ''))
                    formatted_price = f"{price_int:,}".replace(',', ' ')
                    # Заменяем цену в строке
                    line = re.sub(r'(\d+[\s\.,]?\d*)\s*(?:руб|р|₽|RUB)', f"{formatted_price}₽", line, flags=re.IGNORECASE)
                except ValueError:
                    pass
            
            # Выделяем "Цена:" жирным
            line = re.sub(r'(Цена:)', r'*\1*', line, flags=re.IGNORECASE)
            formatted_lines.append(line)
            continue
        
        # Если строка находится в специальном блоке, форматируем её курсивом
        if in_special_block:
            # Не применяем курсив, если строка уже содержит форматирование
            if '*' not in line:
                line = f"_{line}_"
        
        # Форматируем ключевые параметры жирным
        key_params = [
            'Комплект:', 'Аккумулятор:', 'IMEI', 'S/N', 'Серийный номер:', 
            'Мы находимся по адресу:', 'Работаем без выходных:'
        ]
        
        for param in key_params:
            if param.lower() in line.lower():
                # Выделяем параметр жирным
                pattern = re.escape(param)
                line = re.sub(f'({pattern})', r'*\1*', line, flags=re.IGNORECASE)
                break
        
        formatted_lines.append(line)
    
    # Объединяем строки с двойным переносом для лучшей читаемости
    formatted_text = '\n'.join(formatted_lines)
    
    # Заменяем множественные переносы строк на двойные
    formatted_text = re.sub(r'\n{3,}', '\n\n', formatted_text)
    
    # Добавляем пробелы после эмодзи для лучшей читаемости
    formatted_text = re.sub(r'([\U00010000-\U0010ffff])', r'\1 ', formatted_text)
    
    # Исправляем двойные пробелы
    formatted_text = re.sub(r' {2,}', ' ', formatted_text)
    
    return formatted_text

def format_for_telegram(text: str, signature_enabled: bool = True) -> str:
    """
    Форматирует текст специально для Telegram с использованием Markdown V2.
    
    Args:
        text: Исходный текст поста
        signature_enabled: Включена ли подпись
        
    Returns:
        str: Текст, отформатированный для Telegram
    """
    # Сначала применяем общее форматирование
    formatted_text = format_post_text(text)
    
    # Уменьшаем длину разделителя для Telegram
    formatted_text = re.sub(r'—{30}', '—' * 19, formatted_text)
    
    # Добавляем подпись для Telegram
    formatted_text += get_telegram_signature(enabled=signature_enabled)
    
    # Экранируем специальные символы Markdown V2
    special_chars = ['_', '*', '[', ']', '(', ')', '~', '`', '>', '#', '+', '-', '=', '|', '{', '}', '.', '!']
    for char in special_chars:
        formatted_text = formatted_text.replace(char, f'\\{char}')
    
    # Возвращаем форматирование для жирного и курсива
    formatted_text = formatted_text.replace('\\*', '*').replace('\\_', '_')
    
    # Восстанавливаем форматирование для ссылок
    formatted_text = re.sub(r'\\\[(.*?)\\\]\\\((.*?)\\\)', r'[\1](\2)', formatted_text)
    
    return formatted_text

def format_for_instagram(text: str) -> str:
    """
    Форматирует текст для Instagram (без специального форматирования).
    
    Args:
        text: Исходный текст поста
        
    Returns:
        str: Текст, отформатированный для Instagram
    """
    # Применяем общее форматирование
    formatted_text = format_post_text(text)
    
    # Удаляем маркеры форматирования, так как Instagram не поддерживает Markdown
    formatted_text = formatted_text.replace('*', '').replace('_', '')
    
    # Добавляем подпись для Instagram
    formatted_text += get_instagram_signature()
    
    return formatted_text

def format_for_vk(text: str, signature_enabled: bool = True) -> str:
    """
    Форматирует текст для ВКонтакте.
    
    Args:
        text: Исходный текст поста
        signature_enabled: Включена ли подпись
        
    Returns:
        str: Текст, отформатированный для ВКонтакте
    """
    # Применяем общее форматирование
    formatted_text = format_post_text(text)
    
    # Заменяем маркеры Markdown на HTML-теги для ВКонтакте
    formatted_text = formatted_text.replace('*', '')  # ВК не поддерживает жирный шрифт в API
    formatted_text = formatted_text.replace('_', '')  # ВК не поддерживает курсив в API
    
    # Добавляем подпись для ВКонтакте
    formatted_text += get_vk_signature(enabled=signature_enabled)
    
    return formatted_text
