"""
Утилита для форматирования названий товаров для отображения в списке.
"""
import re
from typing import Optional
from app.utils.color_emoji import replace_color_with_emoji


def extract_product_code(name: str) -> Optional[str]:
    """
    Извлекает код товара (4-5 цифр) из названия.
    
    Args:
        name: Название товара
        
    Returns:
        Код товара или None, если не найден
        
    Examples:
        "iPhone XS Max 256Gb Black 1333" -> "1333"
        "iPhone 15 128Gb Pink 3486" -> "3486"
        "iPhone 12 64Gb Red" -> None
    """
    if not name:
        return None
    
    # Ищем 4-5 цифр в конце названия (после пробела или в скобках)
    # Паттерн: пробел + 4-5 цифр в конце строки
    match = re.search(r'\s+(\d{4,5})\s*$', name)
    if match:
        return match.group(1)
    
    # Также проверяем код в скобках в конце
    match = re.search(r'\s*\((\d{4,5})\)\s*$', name)
    if match:
        return match.group(1)
    
    return None


def format_product_name_for_list(name: str) -> str:
    """
    Форматирует название товара в короткий формат для списка.
    
    Args:
        name: Полное название товара (например, "iPhone XS Max 256Gb Black 1333")
        
    Returns:
        Отформатированное название (например, "Xs Max 256Gb ⚫️ 1333")
        
    Examples:
        "iPhone XS Max 256Gb Black 1333" -> "Xs Max 256Gb ⚫️ 1333"
        "iPhone 15 128Gb Pink 3486" -> "15 128Gb 🌸 3486"
        "Apple iPhone 17 Pro Max 512Gb ⚪️ Новый (без RuStore) - 13590000 RUB ВК" -> "17 Pro Max 512Gb ⚪️ (1+1) - 135900₽"
    """
    if not name:
        return name
    
    # Убираем лишние слова для новых товаров
    name = re.sub(r'\s*Apple\s+', ' ', name, flags=re.IGNORECASE)
    name = re.sub(r'\s*Новый\s*', ' ', name, flags=re.IGNORECASE)
    name = re.sub(r'\s*\(без\s+RuStore\)\s*', ' ', name, flags=re.IGNORECASE)
    name = re.sub(r'\s*-\s*\d+\s*RUB?\s*', ' ', name, flags=re.IGNORECASE)
    name = re.sub(r'\s*ВК\s*$', '', name, flags=re.IGNORECASE)
    
    # Извлекаем код товара
    code = extract_product_code(name)
    
    # Убираем код из названия для дальнейшей обработки
    name_without_code = name
    if code:
        # Удаляем код из конца
        name_without_code = re.sub(r'\s+\d{4,5}\s*$', '', name_without_code)
        name_without_code = re.sub(r'\s*\(\d{4,5}\)\s*$', '', name_without_code)
    
    # Убираем "iPhone " из начала (регистронезависимо)
    name_without_code = re.sub(r'^iPhone\s+', '', name_without_code, flags=re.IGNORECASE)
    
    # Преобразуем цвет в эмодзи
    name_without_code = replace_color_with_emoji(name_without_code)
    
    # Добавляем код обратно, если он был
    if code:
        return f"{name_without_code.strip()} {code}"
    
    return name_without_code.strip()
