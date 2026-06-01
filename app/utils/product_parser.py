import re
from typing import Dict, Optional, Tuple
from app.utils.text_extractor import extract_model_and_price


def extract_product_name(text: str) -> Optional[str]:
    """
    Извлекает название товара из заголовка поста (первая строка без эмодзи).
    
    Args:
        text: Текст поста
        
    Returns:
        Название товара или None
    """
    if not text:
        return None
    
    lines = text.strip().split('\n')
    if not lines:
        return None
    
    # Берем первую строку
    first_line = lines[0].strip()
    
    # Удаляем эмодзи
    first_line = re.sub(r'[🔥👍⭐️📱📲💯🎁🎄🎀]+', '', first_line).strip()
    
    # Удаляем эмодзи в конце строки
    first_line = re.sub(r'🔥$', '', first_line).strip()
    
    # Удаляем скобки и их содержимое в конце, если они есть
    first_line = re.sub(r'\s*\([^)]*\)\s*$', '', first_line).strip()
    
    return first_line if first_line else None


def extract_product_description(text: str) -> Optional[str]:
    """
    Извлекает описание товара из текста поста, исключая заголовок, цену и контактную информацию.
    
    Args:
        text: Текст поста
        
    Returns:
        Описание товара или None
    """
    if not text:
        return None
    
    lines = text.strip().split('\n')
    if len(lines) < 2:
        return None
    
    # Пропускаем первую строку (заголовок)
    description_lines = []
    skip_contact_section = False
    found_price = False
    
    # Ключевые слова, после которых начинается контактная информация
    contact_keywords = [
        'мы находимся',
        'работаем без выходных',
        'подписывайся',
        'авито:',
        'телеграм:',
        '📞',
        '⏰',
        '🛒',
        '✈️'
    ]
    
    for i, line in enumerate(lines[1:], start=1):
        line_stripped = line.strip()
        line_lower = line_stripped.lower()
        
        # Пропускаем строку с ценой
        if '💵' in line_stripped or 'цена:' in line_lower or 'цена' in line_lower:
            found_price = True
            continue
        
        # Проверяем, не началась ли контактная информация (но не по эмодзи 📍, так как это может быть адрес)
        if any(keyword in line_lower for keyword in contact_keywords):
            # Если это адрес (📍), включаем его в описание
            if '📍' in line_stripped or 'мы находимся' in line_lower:
                # Включаем адрес и время работы, но не контакты
                if '📞' not in line_stripped and '🛒' not in line_stripped and '✈️' not in line_stripped:
                    description_lines.append(line_stripped)
                    continue
            skip_contact_section = True
        
        # Если встретили разделитель, проверяем контекст
        if re.match(r'^-{5,}$', line_stripped.replace('—', '-')):
            # Если это разделитель после контактной информации, прекращаем
            if skip_contact_section:
                break
            # Иначе включаем разделитель в описание
            description_lines.append(line_stripped)
            continue
        
        # Если еще не в контактной секции, добавляем строку
        if not skip_contact_section and line_stripped:
            description_lines.append(line_stripped)
    
    description = '\n'.join(description_lines)
    
    # Удаляем множественные пустые строки
    description = re.sub(r'\n{3,}', '\n\n', description)
    
    return description.strip() if description.strip() else None


def extract_price(text: str) -> Optional[str]:
    """
    Извлекает цену из текста поста.
    
    Args:
        text: Текст поста
        
    Returns:
        Цена в формате строки (например, "62500₽") или None
    """
    if not text:
        return None
    
    # Используем существующую функцию из text_extractor
    _, price = extract_model_and_price(text)
    
    if price:
        # Убираем пробелы и нормализуем формат
        price = price.replace(' ', '').replace(',', '')
        # Если нет символа валюты, добавляем ₽
        if not any(currency in price for currency in ['руб', 'р', '₽', 'RUB']):
            price = f"{price}₽"
        return price
    
    return None


def detect_category(text: str) -> Optional[str]:
    """
    Автоматически определяет категорию товара по ключевым словам.
    
    Args:
        text: Текст поста
        
    Returns:
        Название категории или None
    """
    if not text:
        return None
    
    text_lower = text.lower()
    
    # Словарь ключевых слов для определения категории
    category_keywords = {
        'смартфоны': [
            'iphone', 'смартфон', 'телефон', 'smartphone', 'android', 
            'samsung', 'xiaomi', 'huawei', 'honor', 'realme', 'oppo', 'vivo'
        ],
        'планшеты': [
            'ipad', 'планшет', 'tablet', 'galaxy tab'
        ],
        'ноутбуки': [
            'macbook', 'ноутбук', 'laptop', 'asus', 'lenovo', 'hp', 'dell'
        ],
        'часы': [
            'apple watch', 'watch', 'часы', 'smartwatch', 'умные часы'
        ],
        'наушники': [
            'airpods', 'наушники', 'headphones', 'earbuds', 'earphones'
        ],
        'компьютеры': [
            'imac', 'mac mini', 'mac pro', 'компьютер', 'pc', 'desktop'
        ]
    }
    
    # Проверяем каждую категорию
    for category, keywords in category_keywords.items():
        if any(keyword in text_lower for keyword in keywords):
            return category
    
    return None


def detect_collection(text: str, category: Optional[str] = None) -> Optional[str]:
    """
    Автоматически определяет подборку товара по ключевым словам.
    
    Args:
        text: Текст поста
        category: Категория товара (для формирования названия подборки)
        
    Returns:
        Название подборки или None
    """
    if not text:
        return None
    
    text_lower = text.lower()
    
    # Ключевые слова для определения подборки
    used_keywords = ['б/у', 'б у', 'бывший в употреблении', 'used', 'second hand', 'вторые руки']
    new_keywords = ['новый', 'новые', 'new', 'оригинал новый', 'в упаковке']
    
    # Определяем, б/у или новый
    is_used = any(keyword in text_lower for keyword in used_keywords)
    is_new = any(keyword in text_lower for keyword in new_keywords)
    
    # Если категория известна, формируем название подборки
    if category:
        if is_used:
            # Для iPhone: "iPhone б/у", для других категорий: "Смартфоны б/у"
            if 'iphone' in text_lower or 'смартфон' in text_lower:
                return "iPhone б/у"
            else:
                return f"{category.capitalize()} б/у"
        elif is_new:
            if 'iphone' in text_lower or 'смартфон' in text_lower:
                return "iPhone новые"
            else:
                return f"{category.capitalize()} новые"
    
    # Если категория не известна, но есть ключевые слова
    if is_used:
        return "б/у"
    elif is_new:
        return "новые"
    
    return None


def parse_product_data(text: str) -> Dict[str, Optional[str]]:
    """
    Главная функция для парсинга данных товара из текста поста.
    
    Args:
        text: Текст поста
        
    Returns:
        Словарь с данными товара:
        {
            'name': название товара,
            'description': описание товара,
            'price': цена,
            'category': категория,
            'collection': подборка
        }
    """
    if not text:
        return {
            'name': None,
            'description': None,
            'price': None,
            'category': None,
            'collection': None
        }
    
    # Извлекаем данные
    name = extract_product_name(text)
    description = extract_product_description(text)
    price = extract_price(text)
    category = detect_category(text)
    collection = detect_collection(text, category)
    
    return {
        'name': name,
        'description': description,
        'price': price,
        'category': category,
        'collection': collection
    }


