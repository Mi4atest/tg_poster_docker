"""
Утилита для преобразования названий цветов iPhone в эмодзи.
Используется для упрощения навигации в inline клавиатуре товаров.
"""
import re
from typing import Dict


# Словарь соответствия названий цветов эмодзи
# Порядок важен: сначала более специфичные названия, потом общие
COLOR_EMOJI_MAP: Dict[str, str] = {
    # Основные цвета (общие)
    "Black": "⚫️",
    "Midnight": "⚫️",
    "Space Black": "⚫️",
    "Graphite": "⚫️",
    "Black Titanium": "⚫️",
    
    "White": "⚪️",
    "Starlight": "⭐",
    "Silver": "⚪️",
    "Cloud White": "⚪️",
    "White Titanium": "⚪️",
    
    "Blue": "🔵",
    "Pacific Blue": "🔵",
    "Ultramarine": "🔵",
    "Mist Blue": "🔵",
    "Deep Blue": "🔵",
    "Sky Blue": "🔵",
    "Sierra Blue": "🔵",
    "Blue Titanium": "🔵",
    
    "Red": "🔴",
    "Product Red": "🔴",
    
    "Green": "🟢",
    "Alpine Green": "🟢",
    "Sage": "🟢",
    "Teal": "🟢",
    "Midnight Green": "🟢",
    
    "Pink": "🌸",
    "Rose Gold": "🌸",
    "Rose": "🌸",
    
    "Yellow": "🟡",
    "Gold": "🟡",
    "Light Gold": "🟡",
    "Desert Titanium": "🟡",
    
    "Purple": "🟣",
    "Lavander": "🟣",
    "Lavender": "🟣",  # Альтернативное написание
    "Deep Purple": "🟣",
    
    "Orange": "🟠",
    "Cosmic Orange": "🟠",
    
    # Titanium модели (специфичные)
    "Natural Titanium": "🔘",  # Для Pro моделей (iPhone 16 Pro и т.д.)
    "Space Gray": "🔘",  # Apple Watch и старые модели (iPhone 8/SE)
}


def replace_color_with_emoji(text: str) -> str:
    """
    Заменяет текстовые названия цветов iPhone на соответствующие эмодзи.
    
    Args:
        text: Текст с названием товара/модели
        
    Returns:
        Текст с замененными цветами на эмодзи
        
    Examples:
        "iPhone 15 128Gb Black" -> "iPhone 15 128Gb ⚫️"
        "iPhone 13 128Gb Pink" -> "iPhone 13 128Gb 🌸"
        "iPhone 12 Pro Max 256Gb Pacific Blue" -> "iPhone 12 Pro Max 256Gb 🔵"
    """
    if not text:
        return text
    
    result = text
    
    # Сортируем ключи по длине (от длинных к коротким), чтобы сначала заменять более специфичные названия
    sorted_colors = sorted(COLOR_EMOJI_MAP.keys(), key=len, reverse=True)
    
    for color in sorted_colors:
        emoji = COLOR_EMOJI_MAP[color]
        # Используем границы слов для точного совпадения
        # Ищем цвет как отдельное слово (регистронезависимо)
        pattern = r'\b' + re.escape(color) + r'\b'
        result = re.sub(pattern, emoji, result, flags=re.IGNORECASE)
    
    return result
