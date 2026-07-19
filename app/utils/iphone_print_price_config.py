"""Конфиг печатного PDF-прайса iPhone (A4)."""
from __future__ import annotations

# Коды б/у-товаров, которые попадают в блок «ОБМЕННЫЕ iPhone».
TRADEIN_PRODUCT_CODES = frozenset({"2614", "4181", "7937", "6935"})

TITLE = "Актуальные цены на Новые iPhone:"
SUBTITLE = "Указанные цены представлены со скидкой за наличный расчёт!"
TRADEIN_SECTION_TITLE = "ОБМЕННЫЕ iPhone (без комплекта)"

# Порядок моделей в прайсе (ключ сортировки → подпись для печати).
MODEL_SORT_ORDER: tuple[str, ...] = (
    "13",
    "13 Pro",
    "13 Pro Max",
    "14",
    "14 Plus",
    "14 Pro",
    "14 Pro Max",
    "15",
    "15 Plus",
    "15 Pro",
    "15 Pro Max",
    "16",
    "16 Plus",
    "16E",
    "16 Pro",
    "16 Pro Max",
    "17E",
    "17",
    "Air",
    "17 Pro",
    "17 Pro Max",
)

# Модели левой колонки (включительно до этой точки по порядку MODEL_SORT_ORDER
# для новых). Правая колонка: начиная с RIGHT_COLUMN_START_MODEL.
RIGHT_COLUMN_START_MODEL = "17 Pro"

# Вёрстка A4 (пункты reportlab)
PAGE_MARGIN_MM = 12.0
TITLE_FONT_SIZE = 14
SUBTITLE_FONT_SIZE = 10
BODY_FONT_SIZE = 8.5
BODY_LEADING = 10.5
SECTION_FONT_SIZE = 10
COLUMN_GAP_MM = 8.0

# Минимальный кегль при автоподгонке под одну страницу
MIN_BODY_FONT_SIZE = 7.0
