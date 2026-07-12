import logging
from sqlalchemy import inspect, text
from sqlalchemy.exc import ProgrammingError

from app.db.database import engine, SessionLocal
from app.config.settings import DATABASE_URL

logger = logging.getLogger(__name__)

def check_and_add_column_if_missing():
    """Проверяет наличие колонки telegram_link и добавляет её, если отсутствует."""
    try:
        inspector = inspect(engine)
        columns = [col['name'] for col in inspector.get_columns('posts')]
        
        if 'telegram_link' not in columns:
            logger.info("Колонка telegram_link отсутствует. Добавляю...")
            with engine.connect() as conn:
                conn.execute(text("ALTER TABLE posts ADD COLUMN telegram_link VARCHAR"))
                conn.commit()
            logger.info("Колонка telegram_link успешно добавлена")
            return True
        else:
            logger.info("Колонка telegram_link уже существует")
            return False
    except Exception as e:
        logger.error(f"Ошибка при проверке/добавлении колонки telegram_link: {e}")
        return False


def check_and_add_max_columns_if_missing():
    """Проверяет наличие Max-колонок и добавляет отсутствующие."""
    try:
        inspector = inspect(engine)
        columns = [col["name"] for col in inspector.get_columns("posts")]
        statements = []
        if "is_published_max" not in columns:
            statements.append("ALTER TABLE posts ADD COLUMN is_published_max BOOLEAN DEFAULT FALSE")
        if "published_max_at" not in columns:
            statements.append("ALTER TABLE posts ADD COLUMN published_max_at TIMESTAMP")
        if "max_link" not in columns:
            statements.append("ALTER TABLE posts ADD COLUMN max_link VARCHAR")
        product_columns = [col["name"] for col in inspector.get_columns("products")]
        if "max_link" not in product_columns:
            statements.append("ALTER TABLE products ADD COLUMN max_link VARCHAR")
        if "max_share_url" not in columns:
            statements.append("ALTER TABLE posts ADD COLUMN max_share_url VARCHAR")
        if "max_share_url" not in product_columns:
            statements.append("ALTER TABLE products ADD COLUMN max_share_url VARCHAR")
        if "avito_item_id" not in columns:
            statements.append("ALTER TABLE posts ADD COLUMN avito_item_id VARCHAR")
        if "avito_url" not in columns:
            statements.append("ALTER TABLE posts ADD COLUMN avito_url VARCHAR")
        if "avito_draft" not in columns:
            statements.append("ALTER TABLE posts ADD COLUMN avito_draft JSON")
        if "is_published_avito" not in columns:
            statements.append("ALTER TABLE posts ADD COLUMN is_published_avito BOOLEAN DEFAULT FALSE")
        if "published_avito_at" not in columns:
            statements.append("ALTER TABLE posts ADD COLUMN published_avito_at TIMESTAMP")
        if "avito_item_id" not in product_columns:
            statements.append("ALTER TABLE products ADD COLUMN avito_item_id VARCHAR")
        if "avito_url" not in product_columns:
            statements.append("ALTER TABLE products ADD COLUMN avito_url VARCHAR")
        if statements:
            with engine.connect() as conn:
                for stmt in statements:
                    conn.execute(text(stmt))
                conn.commit()
            logger.info("Max колонки добавлены: %s", ", ".join(statements))
            return True
        logger.info("Max колонки уже существуют")
        return False
    except Exception as e:
        logger.error(f"Ошибка при проверке/добавлении Max колонок: {e}")
        return False

def init_alembic_version_table():
    """Инициализирует таблицу alembic_version, если её нет."""
    try:
        with engine.connect() as conn:
            # Проверяем существование таблицы alembic_version
            result = conn.execute(text("""
                SELECT EXISTS (
                    SELECT FROM information_schema.tables 
                    WHERE table_name = 'alembic_version'
                )
            """))
            exists = result.scalar()
            
            if not exists:
                logger.info("Таблица alembic_version отсутствует. Создаю...")
                # Создаем таблицу alembic_version
                conn.execute(text("""
                    CREATE TABLE alembic_version (
                        version_num VARCHAR(32) NOT NULL,
                        CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num)
                    )
                """))
                # Устанавливаем начальную ревизию (add_instagram_fields)
                conn.execute(text("""
                    INSERT INTO alembic_version (version_num) 
                    VALUES ('add_instagram_fields')
                """))
                conn.commit()
                logger.info("Таблица alembic_version создана и инициализирована")
                return True
            else:
                logger.info("Таблица alembic_version уже существует")
                return False
    except Exception as e:
        logger.error(f"Ошибка при инициализации alembic_version: {e}")
        return False

def ensure_avito_feed_operations_table() -> bool:
    """Таблица очереди снятия / операций фида Авито."""
    try:
        inspector = inspect(engine)
        if "avito_feed_operations" in inspector.get_table_names():
            return False
        with engine.connect() as conn:
            conn.execute(
                text(
                    """
                    CREATE TABLE avito_feed_operations (
                        id SERIAL PRIMARY KEY,
                        operation_type VARCHAR(32) NOT NULL DEFAULT 'archive',
                        product_id INTEGER,
                        post_id VARCHAR,
                        avito_item_id BIGINT,
                        product_name VARCHAR(255),
                        status VARCHAR(32) NOT NULL DEFAULT 'pending',
                        enqueued_at TIMESTAMP,
                        started_at TIMESTAMP,
                        completed_at TIMESTAMP,
                        failed_at TIMESTAMP,
                        error_message TEXT
                    )
                    """
                )
            )
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_avito_feed_ops_status "
                    "ON avito_feed_operations (status)"
                )
            )
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_avito_feed_ops_product "
                    "ON avito_feed_operations (product_id)"
                )
            )
            conn.commit()
        logger.info("Таблица avito_feed_operations создана")
        return True
    except Exception as e:
        logger.error("Ошибка создания avito_feed_operations: %s", e)
        return False


def ensure_avito_item_id_bigint() -> bool:
    """ID объявлений Авито > 2^31 — нужен BIGINT вместо INTEGER."""
    try:
        with engine.connect() as conn:
            exists = conn.execute(
                text(
                    """
                    SELECT EXISTS (
                        SELECT 1
                        FROM information_schema.tables
                        WHERE table_name = 'avito_feed_operations'
                    )
                    """
                )
            ).scalar()
            if not exists:
                return False
            col_type = conn.execute(
                text(
                    """
                    SELECT data_type
                    FROM information_schema.columns
                    WHERE table_name = 'avito_feed_operations'
                      AND column_name = 'avito_item_id'
                    """
                )
            ).scalar()
            if not col_type:
                return False
            if str(col_type).lower() == "bigint":
                return False
            conn.execute(
                text(
                    "ALTER TABLE avito_feed_operations "
                    "ALTER COLUMN avito_item_id TYPE BIGINT USING avito_item_id::bigint"
                )
            )
            conn.commit()
        logger.info("avito_feed_operations.avito_item_id → BIGINT")
        return True
    except Exception as e:
        logger.error("Ошибка ALTER avito_item_id BIGINT: %s", e)
        return False


def check_and_add_instagram_link_columns_if_missing() -> bool:
    """Проверяет наличие Instagram-колонок и добавляет отсутствующие."""
    try:
        inspector = inspect(engine)
        post_columns = {col["name"] for col in inspector.get_columns("posts")}
        product_columns = {col["name"] for col in inspector.get_columns("products")}
        statements = []
        if "instagram_link" not in post_columns:
            statements.append("ALTER TABLE posts ADD COLUMN instagram_link VARCHAR")
        if "instagram_media_id" not in post_columns:
            statements.append("ALTER TABLE posts ADD COLUMN instagram_media_id VARCHAR")
        if "instagram_link" not in product_columns:
            statements.append("ALTER TABLE products ADD COLUMN instagram_link VARCHAR")
        if "instagram_media_id" not in product_columns:
            statements.append("ALTER TABLE products ADD COLUMN instagram_media_id VARCHAR")
        if statements:
            with engine.connect() as conn:
                for stmt in statements:
                    conn.execute(text(stmt))
                conn.commit()
            logger.info("Instagram колонки добавлены: %s", ", ".join(statements))
            return True
        logger.info("Instagram колонки уже существуют")
        return False
    except Exception as e:
        logger.error("Ошибка при проверке/добавлении Instagram колонок: %s", e)
        return False


def ensure_post_vk_and_queue_columns_if_missing() -> bool:
    """После restore дампа в posts могут отсутствовать поля VK и очереди."""
    try:
        inspector = inspect(engine)
        columns = {col["name"] for col in inspector.get_columns("posts")}
        statements = []
        column_defs = {
            "vk_post_id": "ALTER TABLE posts ADD COLUMN vk_post_id VARCHAR",
            "vk_post_link": "ALTER TABLE posts ADD COLUMN vk_post_link VARCHAR",
            "in_queue": "ALTER TABLE posts ADD COLUMN in_queue BOOLEAN DEFAULT FALSE",
            "queue_status": "ALTER TABLE posts ADD COLUMN queue_status VARCHAR",
            "scheduled_at": "ALTER TABLE posts ADD COLUMN scheduled_at TIMESTAMP",
        }
        for name, stmt in column_defs.items():
            if name not in columns:
                statements.append(stmt)
        if statements:
            with engine.connect() as conn:
                for stmt in statements:
                    conn.execute(text(stmt))
                conn.commit()
            logger.info("VK/queue колонки posts добавлены: %s", ", ".join(statements))
            return True
        logger.info("VK/queue колонки posts уже существуют")
        return False
    except Exception as e:
        logger.error("Ошибка при проверке/добавлении VK/queue колонок posts: %s", e)
        return False


def ensure_new_menu_constructor_columns() -> bool:
    """Колонки конструктора меню: is_service на кнопках, display_label на товарах."""
    try:
        inspector = inspect(engine)
        statements = []
        if "new_menu_buttons" in inspector.get_table_names():
            btn_cols = {col["name"] for col in inspector.get_columns("new_menu_buttons")}
            if "is_service" not in btn_cols:
                statements.append(
                    "ALTER TABLE new_menu_buttons ADD COLUMN is_service BOOLEAN NOT NULL DEFAULT FALSE"
                )
        if "products" in inspector.get_table_names():
            prod_cols = {col["name"] for col in inspector.get_columns("products")}
            if "display_label" not in prod_cols:
                statements.append(
                    "ALTER TABLE products ADD COLUMN display_label VARCHAR(128)"
                )
        if statements:
            with engine.connect() as conn:
                for stmt in statements:
                    conn.execute(text(stmt))
                conn.commit()
            logger.info("Колонки конструктора меню добавлены: %s", ", ".join(statements))
            return True
        return False
    except Exception as e:
        logger.error("Ошибка ensure_new_menu_constructor_columns: %s", e)
        return False


def ensure_posts_created_at_index() -> bool:
    """Index for archive date filters and sorting."""
    try:
        inspector = inspect(engine)
        if "posts" not in inspector.get_table_names():
            return False
        indexes = {idx["name"] for idx in inspector.get_indexes("posts")}
        if "ix_posts_created_at" in indexes:
            return False
        with engine.connect() as conn:
            conn.execute(text("CREATE INDEX ix_posts_created_at ON posts (created_at)"))
            conn.commit()
        logger.info("Индекс ix_posts_created_at создан")
        return True
    except Exception as e:
        logger.error("Ошибка ensure_posts_created_at_index: %s", e)
        return False


def ensure_product_price_history_schema() -> bool:
    """price_changed_at, product_price_history и backfill для существующих товаров."""
    try:
        inspector = inspect(engine)
        if "products" not in inspector.get_table_names():
            return False
        statements = []
        prod_cols = {col["name"] for col in inspector.get_columns("products")}
        if "price_changed_at" not in prod_cols:
            statements.append(
                "ALTER TABLE products ADD COLUMN price_changed_at TIMESTAMP"
            )
        tables = set(inspector.get_table_names())
        if "product_price_history" not in tables:
            statements.extend([
                """
                CREATE TABLE product_price_history (
                    id SERIAL PRIMARY KEY,
                    product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
                    old_price VARCHAR,
                    new_price VARCHAR NOT NULL,
                    changed_at TIMESTAMP NOT NULL,
                    source VARCHAR NOT NULL DEFAULT 'manual'
                )
                """,
                """
                CREATE INDEX ix_product_price_history_product_changed
                ON product_price_history (product_id, changed_at)
                """,
            ])
        with engine.connect() as conn:
            for stmt in statements:
                conn.execute(text(stmt))
            conn.execute(
                text(
                    "UPDATE products SET price_changed_at = created_at "
                    "WHERE price_changed_at IS NULL"
                )
            )
            conn.execute(
                text(
                    """
                    INSERT INTO product_price_history (product_id, old_price, new_price, changed_at, source)
                    SELECT p.id, NULL, COALESCE(p.price, ''), p.created_at, 'publication'
                    FROM products p
                    WHERE COALESCE(p.price, '') != ''
                      AND NOT EXISTS (
                          SELECT 1 FROM product_price_history h WHERE h.product_id = p.id
                      )
                    """
                )
            )
            conn.commit()
        if statements:
            logger.info("Схема истории цен добавлена/обновлена")
            return True
        logger.info("Схема истории цен актуальна (backfill выполнен)")
        return False
    except Exception as e:
        logger.error("Ошибка ensure_product_price_history_schema: %s", e)
        return False


def ensure_database_schema():
    """Обеспечивает актуальность схемы базы данных."""
    logger.info("Проверка схемы базы данных...")

    check_and_add_column_if_missing()
    check_and_add_max_columns_if_missing()
    check_and_add_instagram_link_columns_if_missing()
    ensure_post_vk_and_queue_columns_if_missing()

    ensure_avito_feed_operations_table()
    ensure_avito_item_id_bigint()

    ensure_new_menu_constructor_columns()
    ensure_posts_created_at_index()
    ensure_product_price_history_schema()

    init_alembic_version_table()

    logger.info("Проверка схемы базы данных завершена")

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    ensure_database_schema()

