"""
Утилита для парсинга моделей iPhone из названий товаров.
"""
import re
from typing import Optional, Tuple, Dict, List


# Словарь моделей iPhone с их вариантами
IPHONE_MODELS = {
    "iPhone X": {
        "keywords": ["iphone x", "iphone x "],
        "exclude": ["xs", "xr", "11", "12", "13", "14", "15"],
        "variants": ["x"]
    },
    "iPhone XS": {
        "keywords": ["iphone xs", "iphone xs "],
        "exclude": ["xs max", "xr", "11", "12", "13", "14", "15"],
        "variants": ["xs"]
    },
    "iPhone XS Max": {
        "keywords": ["iphone xs max", "iphone xs max "],
        "exclude": [],
        "variants": ["xs max"]
    },
    "iPhone XR": {
        "keywords": ["iphone xr", "iphone xr "],
        "exclude": ["xs", "11", "12", "13", "14", "15"],
        "variants": ["xr"]
    },
    "iPhone 11": {
        "keywords": ["iphone 11", "iphone 11 "],
        "exclude": ["11 pro", "11 pro max", "12", "13", "14", "15"],
        "variants": ["11"]
    },
    "iPhone 11 Pro": {
        "keywords": ["iphone 11 pro", "iphone 11 pro "],
        "exclude": ["11 pro max", "12", "13", "14", "15"],
        "variants": ["11 pro"]
    },
    "iPhone 11 Pro Max": {
        "keywords": ["iphone 11 pro max", "iphone 11 pro max "],
        "exclude": [],
        "variants": ["11 pro max"]
    },
    "iPhone 12": {
        "keywords": ["iphone 12", "iphone 12 "],
        "exclude": ["12 mini", "12 pro", "12 pro max", "13", "14", "15"],
        "variants": ["12"]
    },
    "iPhone 12 mini": {
        "keywords": ["iphone 12 mini", "iphone 12 mini "],
        "exclude": ["12 pro", "12 pro max", "13", "14", "15"],
        "variants": ["12 mini"]
    },
    "iPhone 12 Pro": {
        "keywords": ["iphone 12 pro", "iphone 12 pro "],
        "exclude": ["12 pro max", "13", "14", "15"],
        "variants": ["12 pro"]
    },
    "iPhone 12 Pro Max": {
        "keywords": ["iphone 12 pro max", "iphone 12 pro max "],
        "exclude": [],
        "variants": ["12 pro max"]
    },
    "iPhone 13": {
        "keywords": ["iphone 13", "iphone 13 "],
        "exclude": ["13 mini", "13 pro", "13 pro max", "14", "15"],
        "variants": ["13"]
    },
    "iPhone 13 mini": {
        "keywords": ["iphone 13 mini", "iphone 13 mini "],
        "exclude": ["13 pro", "13 pro max", "14", "15"],
        "variants": ["13 mini"]
    },
    "iPhone 13 Pro": {
        "keywords": ["iphone 13 pro", "iphone 13 pro "],
        "exclude": ["13 pro max", "14", "15"],
        "variants": ["13 pro"]
    },
    "iPhone 13 Pro Max": {
        "keywords": ["iphone 13 pro max", "iphone 13 pro max "],
        "exclude": [],
        "variants": ["13 pro max"]
    },
    "iPhone 14": {
        "keywords": ["iphone 14", "iphone 14 "],
        "exclude": ["14 plus", "14 pro", "14 pro max", "15"],
        "variants": ["14"]
    },
    "iPhone 14 Plus": {
        "keywords": ["iphone 14 plus", "iphone 14 plus "],
        "exclude": ["14 pro", "14 pro max", "15"],
        "variants": ["14 plus"]
    },
    "iPhone 14 Pro": {
        "keywords": ["iphone 14 pro", "iphone 14 pro "],
        "exclude": ["14 pro max", "15"],
        "variants": ["14 pro"]
    },
    "iPhone 14 Pro Max": {
        "keywords": ["iphone 14 pro max", "iphone 14 pro max "],
        "exclude": [],
        "variants": ["14 pro max"]
    },
    "iPhone 15": {
        "keywords": ["iphone 15", "iphone 15 "],
        "exclude": ["15 plus", "15 pro", "15 pro max"],
        "variants": ["15"]
    },
    "iPhone 15 Plus": {
        "keywords": ["iphone 15 plus", "iphone 15 plus "],
        "exclude": ["15 pro", "15 pro max"],
        "variants": ["15 plus"]
    },
    "iPhone 15 Pro": {
        "keywords": ["iphone 15 pro", "iphone 15 pro "],
        "exclude": ["15 pro max"],
        "variants": ["15 pro"]
    },
    "iPhone 15 Pro Max": {
        "keywords": ["iphone 15 pro max", "iphone 15 pro max "],
        "exclude": [],
        "variants": ["15 pro max"]
    },
    "iPhone 16": {
        "keywords": ["iphone 16", "iphone 16 "],
        "exclude": ["16 plus", "16 pro", "16 pro max", "16e", "16 e", "17"],
        "variants": ["16"]
    },
    "iPhone 16E": {
        "keywords": ["iphone 16e", "iphone 16 e", "iphone 16e "],
        "exclude": ["16 plus", "16 pro", "16 pro max", "17"],
        "variants": ["16e", "16 e"]
    },
    "iPhone 16 Plus": {
        "keywords": ["iphone 16 plus", "iphone 16 plus "],
        "exclude": ["16 pro", "16 pro max", "16e", "16 e", "17"],
        "variants": ["16 plus"]
    },
    "iPhone 16 Pro": {
        "keywords": ["iphone 16 pro", "iphone 16 pro "],
        "exclude": ["16 pro max", "17"],
        "variants": ["16 pro"]
    },
    "iPhone 16 Pro Max": {
        "keywords": ["iphone 16 pro max", "iphone 16 pro max "],
        "exclude": [],
        "variants": ["16 pro max"]
    },
    "iPhone 17": {
        "keywords": ["iphone 17", "iphone 17 "],
        "exclude": ["17 pro", "17 pro max", "17e", "17 e"],
        "variants": ["17"]
    },
    "iPhone 17E": {
        "keywords": ["iphone 17e", "iphone 17 e", "iphone 17e "],
        "exclude": ["17 pro", "17 pro max"],
        "variants": ["17e", "17 e"]
    },
    "iPhone 17 Pro": {
        "keywords": ["iphone 17 pro", "iphone 17 pro "],
        "exclude": ["17 pro max", "17e", "17 e"],
        "variants": ["17 pro"]
    },
    "iPhone 17 Pro Max": {
        "keywords": ["iphone 17 pro max", "iphone 17 pro max "],
        "exclude": [],
        "variants": ["17 pro max"]
    },
    "iPhone Air": {
        "keywords": ["iphone air", "iphone air "],
        "exclude": [],
        "variants": ["air"]
    },
    "iPhone SE 2020": {
        "keywords": ["iphone se 2020", "iphone se (2020)", "iphone se 2"],
        "exclude": ["se 2022", "se 3"],
        "variants": ["se 2020", "se (2020)", "se 2"]
    },
    "iPhone SE 2022": {
        "keywords": ["iphone se 2022", "iphone se (2022)", "iphone se 3"],
        "exclude": [],
        "variants": ["se 2022", "se (2022)", "se 3"]
    },
}


def parse_iphone_model(name: str) -> Optional[str]:
    """
    Определяет модель iPhone из названия товара.
    
    Args:
        name: Название товара
        
    Returns:
        Название модели iPhone или None, если не удалось определить
    """
    if not name:
        return None
    
    name_lower = name.lower().strip()
    
    # Сначала проверяем более специфичные модели (Pro Max, Pro, mini, Plus)
    # Сортируем модели по приоритету: сначала самые специфичные (Pro Max, Pro, mini, Plus), потом базовые
    priority_order = {
        "pro max": 1,
        "pro": 2,
        "mini": 3,
        "plus": 4,
        "air": 5
    }
    
    def get_priority(model_name):
        name_lower_model = model_name.lower()
        for key, priority in priority_order.items():
            if key in name_lower_model:
                return priority
        return 10  # Базовые модели
    
    sorted_models = sorted(
        IPHONE_MODELS.items(),
        key=lambda x: (get_priority(x[0]), -max(len(kw) for kw in x[1]["keywords"])),
    )
    
    for model_name, model_info in sorted_models:
        # Проверяем ключевые слова
        for keyword in model_info["keywords"]:
            keyword_clean = keyword.strip()
            # Используем более точное совпадение
            # Для коротких ключевых слов (например, "iphone 13") проверяем, что после них идет пробел или конец строки
            if keyword_clean.endswith(" "):
                # Ключевое слово заканчивается пробелом - ищем точное совпадение
                if name_lower.startswith(keyword_clean) or f" {keyword_clean}" in name_lower:
                    # Проверяем исключения (более специфичные модели)
                    excluded = False
                    for exclude in model_info["exclude"]:
                        exclude_clean = exclude.strip()
                        # Проверяем, что исключение присутствует в названии
                        if exclude_clean in name_lower:
                            # Убеждаемся, что это не часть другого слова
                            exclude_pattern = r'\b' + re.escape(exclude_clean) + r'\b'
                            if re.search(exclude_pattern, name_lower):
                                excluded = True
                                break
                    
                    if not excluded:
                        return model_name
            else:
                # Ключевое слово без пробела в конце - используем границы слов
                keyword_pattern = r'\b' + re.escape(keyword_clean) + r'\b'
                if re.search(keyword_pattern, name_lower):
                    # Проверяем исключения (более специфичные модели)
                    excluded = False
                    for exclude in model_info["exclude"]:
                        exclude_clean = exclude.strip()
                        exclude_pattern = r'\b' + re.escape(exclude_clean) + r'\b'
                        if re.search(exclude_pattern, name_lower):
                            excluded = True
                            break
                    
                    if not excluded:
                        return model_name
    
    return None


def group_products_by_model(products: List[Dict]) -> Dict[str, List[Dict]]:
    """
    Группирует товары по моделям iPhone.
    
    Args:
        products: Список товаров
        
    Returns:
        Словарь {model_name: [products]}
    """
    grouped = {}
    
    for product in products:
        name = product.get("name", "")
        model = parse_iphone_model(name)
        
        if model:
            if model not in grouped:
                grouped[model] = []
            grouped[model].append(product)
        else:
            # Товары без определенной модели попадают в "Другие"
            if "Другие" not in grouped:
                grouped["Другие"] = []
            grouped["Другие"].append(product)
    
    return grouped


def get_model_display_name(model: str) -> str:
    """
    Возвращает короткое название модели для отображения в кнопках.
    
    Args:
        model: Полное название модели
        
    Returns:
        Короткое название
    """
    # Убираем "iPhone " из начала
    if model.startswith("iPhone "):
        return model.replace("iPhone ", "")
    return model


def sort_models_for_display(models: List[str]) -> List[str]:
    """
    Сортирует модели iPhone для отображения в правильном порядке.
    
    Args:
        models: Список названий моделей
        
    Returns:
        Отсортированный список
    """
    # Порядок моделей (от старых к новым): в рамках версии — mini/base, затем Plus, Pro, Pro Max
    model_order = [
        "iPhone X",
        "iPhone XS",
        "iPhone XS Max",
        "iPhone XR",
        "iPhone 11",
        "iPhone 11 Pro",
        "iPhone 11 Pro Max",
        "iPhone 12 mini",
        "iPhone 12",
        "iPhone 12 Pro",
        "iPhone 12 Pro Max",
        "iPhone 13 mini",
        "iPhone 13",
        "iPhone 13 Pro",
        "iPhone 13 Pro Max",
        "iPhone 14",
        "iPhone 14 Plus",
        "iPhone 14 Pro",
        "iPhone 14 Pro Max",
        "iPhone 15",
        "iPhone 15 Plus",
        "iPhone 15 Pro",
        "iPhone 15 Pro Max",
        "iPhone 16",
        "iPhone 16E",
        "iPhone 16 Plus",
        "iPhone 16 Pro",
        "iPhone 16 Pro Max",
        "iPhone Air",
        "iPhone 17",
        "iPhone 17E",
        "iPhone 17 Pro",
        "iPhone 17 Pro Max",
        "iPhone SE 2020",
        "iPhone SE 2022",
        "Другие"
    ]
    
    # Создаем словарь для быстрого поиска индекса
    order_dict = {model: i for i, model in enumerate(model_order)}
    
    # Сортируем модели
    sorted_models = sorted(
        models,
        key=lambda m: order_dict.get(m, 999)  # Неизвестные модели в конец
    )
    
    return sorted_models


def get_main_iphone_versions() -> List[str]:
    """
    Возвращает список основных версий iPhone (без вариантов).
    
    Returns:
        Список основных версий: ["X", "11", "12", "13", "14", "15", "16", "17", "SE", "Air"]
    """
    return ["X", "11", "12", "13", "14", "15", "16", "17", "SE", "Air"]


def get_models_for_version(version: str, grouped_products: Dict[str, List[Dict]]) -> Dict[str, List[Dict]]:
    """
    Возвращает модели для конкретной версии iPhone.
    
    Args:
        version: Основная версия (например, "13", "14", "15")
        grouped_products: Словарь всех сгруппированных товаров
        
    Returns:
        Словарь {model_name: [products]} для указанной версии
    """
    result = {}
    
    for model_name, products in grouped_products.items():
        if model_name == "Другие":
            continue
        
        # Проверяем, относится ли модель к указанной версии
        model_lower = model_name.lower()
        version_lower = version.lower()
        
        # Для SE и Air проверяем отдельно
        if version_lower == "se":
            if "se" in model_lower:
                result[model_name] = products
        elif version_lower == "air":
            if "air" in model_lower:
                result[model_name] = products
        elif version_lower == "x":
            # Для X проверяем X, XS, XS Max, XR
            if model_lower.startswith("iphone x") and not any(v in model_lower for v in ["11", "12", "13", "14", "15", "16", "17"]):
                result[model_name] = products
        else:
            # Для остальных версий проверяем начало названия
            # Для версии 16 также включаем 16E
            if version_lower == "16":
                if f"iphone {version_lower}" in model_lower:
                    result[model_name] = products
            else:
                if f"iphone {version_lower}" in model_lower:
                    result[model_name] = products
    
    return result


def group_by_main_version(grouped_products: Dict[str, List[Dict]]) -> Dict[str, Dict[str, List[Dict]]]:
    """
    Группирует товары по основным версиям iPhone.
    
    Args:
        grouped_products: Словарь {model_name: [products]}
        
    Returns:
        Словарь {version: {model_name: [products]}}
    """
    result = {}
    versions = get_main_iphone_versions()
    
    for version in versions:
        models = get_models_for_version(version, grouped_products)
        if models:
            result[version] = models
    
    # Добавляем "Другие" если есть
    if "Другие" in grouped_products:
        result["Другие"] = {"Другие": grouped_products["Другие"]}
    
    return result


# ——— Парсинг для новых товаров: память, тип хранилища, цвет ———

# Эмодзи цветов, встречающиеся в названиях (для извлечения цвета)
_IPHONE_COLOR_EMOJI = ["🟣", "🟢", "🔵", "⚪️", "⚫️", "🟠", "🟡", "🌸", "🔴"]
# Паттерны типа хранилища в названии
_STORAGE_PATTERNS = [
    (r"\(?\s*esim\s*\)?", "esim"),
    (r"\(?\s*1\+1\s*\)?", "1+1"),
    (r"\(?\s*2\s*sim\s*\)?", "2sim"),
    (r"\(?\s*1\+1\s*\)?", "1+1"),
]


def parse_iphone_memory(name: str) -> Optional[str]:
    """
    Извлекает объём памяти из названия товара.
    Returns: "256", "512", "1Tb" или None.
    """
    if not name:
        return None
    # 1Tb / 1 Tb
    m = re.search(r"1\s*Tb", name, re.IGNORECASE)
    if m:
        return "1Tb"
    # 256Gb, 512Gb, 128Gb и т.д.
    m = re.search(r"(\d+)\s*Gb", name, re.IGNORECASE)
    if m:
        return m.group(1)
    return None


def parse_iphone_storage_type(name: str) -> Optional[str]:
    """
    Извлекает тип сим-карты/хранилища: esim, 1+1, 2sim.

    «Sim+eSim» / «Sim + eSim» — синоним 1+1; проверяется раньше голого «esim»,
    иначе подстрока esim внутри Sim+eSim ложно даёт esim.
    """
    if not name:
        return None
    name_lower = name.lower()
    # Sim+eSim / 1+1 — до проверки esim (Sim+eSim содержит «esim»)
    if re.search(r"sim\s*\+\s*esim", name_lower) or "1+1" in name_lower:
        return "1+1"
    if "2sim" in name_lower or "2 sim" in name_lower:
        return "2sim"
    if "esim" in name_lower:
        return "esim"
    return None


def parse_iphone_color_key(name: str) -> Optional[str]:
    """
    Извлекает ключ цвета из названия (эмодзи или текстовый идентификатор).
    Возвращает один из: "🟣","🟢","🔵","⚪️","⚫️","🟠","🟡","🌸","🔴" или None.
    """
    if not name:
        return None
    for em in _IPHONE_COLOR_EMOJI:
        if em in name:
            return em
    # Текстовые варианты (латиница)
    color_text_map = [
        ("purple", "🟣"), ("green", "🟢"), ("blue", "🔵"), ("white", "⚪️"),
        ("black", "⚫️"), ("orange", "🟠"), ("rose gold", "🌸"), ("rose", "🌸"),
        ("yellow", "🟡"), ("gold", "🟡"),
        ("pink", "🌸"), ("red", "🔴"), ("midnight", "⚫️"), ("starlight", "⭐"),
    ]
    name_lower = name.lower()
    for kw, em in color_text_map:
        if kw in name_lower:
            return em
    return None


def parse_iphone_details(name: str) -> Dict[str, Optional[str]]:
    """
    Извлекает из названия товара поля для навигации новых товаров.
    Returns: {"model": str|None, "memory": "256"|"512"|"1Tb"|None, "storage": "esim"|"1+1"|"2sim"|None, "color": emoji|None}.
    """
    return {
        "model": parse_iphone_model(name),
        "memory": parse_iphone_memory(name),
        "storage": parse_iphone_storage_type(name),
        "color": parse_iphone_color_key(name),
    }


def get_iphone_version_from_model(model_name: str) -> Optional[str]:
    """
    Возвращает основную версию (12, 13, 14, 15, 16, 17, SE, Air) по названию модели.
    """
    if not model_name:
        return None
    ml = model_name.lower()
    if "air" in ml:
        return "17"  # iPhone Air считаем в составе 17 для новых
    if "se" in ml:
        return "SE"
    if "16e" in ml or "16 e" in ml:
        return "16"  # iPhone 16E относится к версии 16
    if "17e" in ml or "17 e" in ml:
        return "17"  # iPhone 17E относится к версии 17
    m = re.search(r"iphone\s*(\d+)", ml)
    if m:
        return m.group(1)
    if ml.startswith("iphone x") and "11" not in ml and "12" not in ml:
        return "X"
    return None


def get_short_model_key_for_new(model_name: str) -> str:
    """
    Возвращает короткий ключ модели для callback_data: Air -> air, 17 -> 17, 17 Pro -> 17pro, 17 Pro Max -> 17promax.
    """
    if not model_name:
        return ""
    d = get_model_display_name(model_name)  # "17 Pro Max", "Air", "17"
    d = d.strip()
    if not d:
        return ""
    return d.replace(" ", "_").lower().replace("-", "_")

