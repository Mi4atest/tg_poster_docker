"""Константы вёрстки PDF-ценников (подставка 4×6 см, контент ~3,85×5,75 см)."""

# Ячейка сетки (рамка подставки)
CELL_WIDTH_MM = 40.0
CELL_HEIGHT_MM = 60.0

# Область контента внутри ячейки
CONTENT_WIDTH_MM = 38.5
CONTENT_HEIGHT_MM = 57.5
CONTENT_INSET_MM = (CELL_WIDTH_MM - CONTENT_WIDTH_MM) / 2.0

# Высота зон контента (сверху вниз), мм
ZONE_HEADER_MM = 20.0
ZONE_DESC_MM = 16.5
ZONE_PRICE_MM = 17.0
ZONE_FOOTER_MM = CONTENT_HEIGHT_MM - ZONE_HEADER_MM - ZONE_DESC_MM - ZONE_PRICE_MM  # 4.0

COLS = 4
ROWS = 4
TAGS_PER_PAGE = COLS * ROWS

# Поля и зазоры на A4 (210×297 мм)
PAGE_MARGIN_LEFT_MM = 5.0
PAGE_MARGIN_TOP_MM = 8.5
CELL_GAP_H_MM = 0.0
CELL_GAP_V_MM = 0.0

# Шрифты (pt)
TITLE_FONT_MAX = 8.5
TITLE_FONT_MIN = 5.5
SUBTITLE_FONT = 6.5
DESC_FONT_MAX = 5.0
DESC_FONT_MIN = 3.8
CASH_LABEL_FONT = 5.5
CASH_PRICE_FONT = 13.0
STRIKE_LABEL_FONT = 5.0
STRIKE_PRICE_FONT = 8.5
FOOTER_FONT = 4.5

# Подписи цен — две строки, чтобы не наслаиваться на числа
CASH_LABEL_LINES = ("Цена за", "наличные")
STRIKE_LABEL_LINES = ("Цена без", "скидки")
SIGNATURE_TEXT = "Подпись ___________"

# Legacy (для совместимости, если где-то импортируется)
CASH_LABEL = " ".join(CASH_LABEL_LINES)
STRIKE_LABEL = " ".join(STRIKE_LABEL_LINES)
