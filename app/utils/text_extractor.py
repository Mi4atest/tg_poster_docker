import re
from typing import Tuple, Optional

def extract_model_and_price(text: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Извлекает название модели и цену из текста поста.
    
    Примеры текстов:
    - 🔥iPhone 15 128Gb Black 3486 (Б/у, Оригинал)🔥\n\nЦена: 49900р.\n\nАктивирован 17.01.2025\nТелефон оригинал...
    - 🔥Apple Watch Series SE (2nd Gen), 44mm Midnight (Б/у, Оригинал)🔥\n\nЦена: 19900р.\n\nСостояние: 9/10...
    - 🔥 Новые планшеты iPad 10🔥 \n\niPad 10th Gen (2022) 64Gb Wi-Fi\nЦена: 39900 руб.\n\nВ наличии...
    
    Args:
        text: Текст поста
        
    Returns:
        Tuple[Optional[str], Optional[str]]: (название модели, цена)
    """
    # Извлечение названия модели
    model_name = None
    
    # Ищем название модели в первой строке (обычно это заголовок)
    lines = text.strip().split('\n')
    if lines:
        first_line = lines[0].strip()
        # Удаляем эмодзи и лишние символы
        first_line = re.sub(r'[🔥👍⭐️📱📲💯🎁🎄🎀]+', '', first_line).strip()
        
        # Проверяем, есть ли в первой строке название модели
        if any(keyword in first_line.lower() for keyword in ['iphone', 'ipad', 'macbook', 'apple watch', 'airpods']):
            # Удаляем скобки и их содержимое, если они есть в конце строки
            model_name = re.sub(r'\s*\([^)]*\)\s*$', '', first_line)
            # Удаляем эмодзи в конце строки
            model_name = re.sub(r'🔥$', '', model_name).strip()
    
    # Если не нашли в первой строке, ищем в тексте
    if not model_name:
        # Ищем строки, которые могут содержать название модели
        model_patterns = [
            r'(iPhone\s+\d+\s+(?:Pro|Pro Max|Plus|mini)?\s+\d+Gb\s+\w+)',
            r'(iPad\s+(?:Pro|Air|mini)?\s+\d+(?:th Gen)?\s+\d+Gb)',
            r'(MacBook\s+(?:Pro|Air)\s+\d+(?:\.\d+)?(?:\s+inch)?)',
            r'(Apple\s+Watch\s+Series\s+\w+(?:\s+\d+mm)?)',
            r'(AirPods\s+(?:Pro|Max)?(?:\s+\d+)?)'
        ]
        
        for pattern in model_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                model_name = match.group(1)
                break
    
    # Извлечение цены
    price = None
    
    # Ищем цену в формате "Цена: XXXXX" или "XXXXX руб" или "XXXXX р."
    price_patterns = [
        r'Цена:?\s*(\d+[\s\.,]?\d*)\s*(?:руб|р|₽|RUB)',
        r'(\d+[\s\.,]?\d*)\s*(?:руб|р|₽|RUB)',
        r'стоимость:?\s*(\d+[\s\.,]?\d*)\s*(?:руб|р|₽|RUB)',
        r'цена\s*-\s*(\d+[\s\.,]?\d*)\s*(?:руб|р|₽|RUB)'
    ]
    
    for pattern in price_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            # Удаляем пробелы и заменяем запятую на точку
            price = match.group(1).replace(' ', '').replace(',', '.')
            # Добавляем "₽" если его нет
            if not any(currency in price for currency in ['руб', 'р', '₽', 'RUB']):
                price = f"{price}₽"
            break
    
    return model_name, price
