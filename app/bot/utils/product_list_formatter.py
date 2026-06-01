"""
Утилита для форматирования полного списка товаров для отображения в Telegram.
"""
import re
from typing import List, Dict, Optional, Tuple
from app.utils.iphone_parser import group_products_by_model, sort_models_for_display, get_iphone_version_from_model
from app.utils.product_formatter import format_product_name_for_list


def _extract_memory_mb(name: str) -> int:
    """Извлекает объём памяти в условных единицах для сортировки (64Gb->64, 128Gb->128, 256Gb->256, 512Gb->512, 1Tb->1024)."""
    if not name:
        return 0
    # 1Tb / 1 Tb — после 512Gb
    match_tb = re.search(r'1\s*Tb', name, re.IGNORECASE)
    if match_tb:
        return 1024
    match = re.search(r'(\d+)\s*Gb', name, re.IGNORECASE)
    if match:
        return int(match.group(1))
    return 0


def _parse_price_number(price: str) -> float:
    """Извлекает числовое значение цены из строки (например, '19900₽' -> 19900)."""
    if not price:
        return 0.0
    cleaned = re.sub(r'[^\d.,]', '', str(price)).replace(',', '.')
    try:
        return float(cleaned) if cleaned else 0.0
    except ValueError:
        return 0.0


def _product_sort_key(product: Dict) -> Tuple[int, float]:
    """Ключ сортировки: сначала по памяти (возр.), затем по цене (возр.)."""
    name = product.get('name', '')
    price = product.get('price', '')
    return (_extract_memory_mb(name), _parse_price_number(price))


def format_full_products_list(
    products: List[Dict],
    exclude_product_id: Optional[int] = None
) -> str:
    """
    Форматирует полный список товаров для отображения в Telegram.
    
    Args:
        products: Список товаров
        exclude_product_id: ID товара, который нужно исключить из списка (опционально)
        
    Returns:
        Отформатированный текст списка товаров
        
    Format:
        [название](telegram_link) - [цена] [ВК](vk_link)
        Разделители между моделями без лишних отступов
    """
    if not products:
        return "📦 Список товаров пуст."
    
    # Фильтруем товары, если нужно исключить один
    filtered_products = products
    if exclude_product_id is not None:
        filtered_products = [p for p in products if p.get('id') != exclude_product_id]
    
    if not filtered_products:
        return "📦 Нет других товаров."
    
    # Группируем товары по моделям
    grouped_products = group_products_by_model(filtered_products)
    
    # Сортируем модели от старых к новым
    sorted_models = sort_models_for_display(list(grouped_products.keys()))
    
    # Формируем список
    lines = []
    previous_version = None
    
    for model in sorted_models:
        model_products = grouped_products[model]
        # Сортировка внутри модели: сначала по памяти (от меньшей к большей), затем по цене (от меньшей к большей)
        model_products = sorted(model_products, key=_product_sort_key)
        
        # Определяем версию модели (X, 11, 12, 13, 14, 15, 16, 17, SE, Air)
        current_version = get_iphone_version_from_model(model)
        
        # Добавляем разделитель только между версиями (кроме первой), а не между моделями внутри одной версии
        if previous_version is not None and current_version != previous_version:
            lines.append("━━━━━━━━━━━━━━")
        
        # Добавляем товары модели
        for product in model_products:
            # Форматируем название товара
            formatted_name = format_product_name_for_list(product.get('name', 'Без названия'))
            
            # Получаем цену
            price = product.get('price', '')
            if not price:
                price = 'Цена не указана'
            
            # Формируем строку с ссылками
            telegram_link = product.get('telegram_link')
            vk_link = product.get('vk_product_link')
            
            # Собираем строку
            line_parts = []
            
            # Название с ссылкой на Telegram или без ссылки
            if telegram_link:
                line_parts.append(f'<a href="{telegram_link}">{formatted_name}</a>')
            else:
                line_parts.append(formatted_name)
            
            # Цена
            line_parts.append(f"- {price}")
            
            # Ссылка на ВК
            if vk_link:
                line_parts.append(f'<a href="{vk_link}">ВК</a>')
            
            lines.append(" ".join(line_parts))
        
        previous_version = current_version
    
    # Объединяем все строки, отображаем весь список
    result = "\n".join(lines)
    return result
