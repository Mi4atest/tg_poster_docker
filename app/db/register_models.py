"""Импорт всех ORM-моделей в metadata до create_all / Alembic.

Без этого SQLAlchemy не знает о таблицах, на которые ссылаются FK
(например products.custom_button_id → new_menu_buttons).
"""
from app.api.models.post import (  # noqa: F401
    AppSettings,
    Post,
    PublicationLog,
    PublicationQueue,
)
from app.api.models.product import Product  # noqa: F401
from app.api.models.product_price_history import ProductPriceHistory  # noqa: F401
from app.api.models.product_sale import ProductSale  # noqa: F401
from app.api.models.new_menu_button import NewMenuButton  # noqa: F401
from app.api.models.story import Story, StoryPublicationLog  # noqa: F401
from app.api.models.avito_feed_operation import AvitoFeedOperation  # noqa: F401
from app.api.models.avito_market_snapshot import AvitoMarketSnapshot  # noqa: F401
from app.api.models.avito_market_request_log import AvitoMarketRequestLog  # noqa: F401
from app.api.models.avito_market_watchlist_item import AvitoMarketWatchlistItem  # noqa: F401
from app.api.models.evening_report import EveningReportRecord  # noqa: F401
from app.api.models.shop_note import ShopNote  # noqa: F401
