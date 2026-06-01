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
        inspector = inspect(engine)
        if "avito_feed_operations" not in inspector.get_table_names():
            return False
        cols = {c["name"]: c for c in inspector.get_columns("avito_feed_operations")}
        col = cols.get("avito_item_id")
        if not col:
            return False
        col_type = str(col.get("type", "")).upper()
        if "BIGINT" in col_type:
            return False
        with engine.connect() as conn:
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


def ensure_database_schema():
    """Обеспечивает актуальность схемы базы данных."""
    logger.info("Проверка схемы базы данных...")
    
    # Проверяем и добавляем колонку telegram_link
    check_and_add_column_if_missing()
    check_and_add_max_columns_if_missing()
    ensure_avito_feed_operations_table()
    ensure_avito_item_id_bigint()
    
    # Инициализируем alembic_version, если нужно
    init_alembic_version_table()
    
    logger.info("Проверка схемы базы данных завершена")

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    ensure_database_schema()

