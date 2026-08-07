import logging
from sqlalchemy import inspect, text

from app.db.database import engine

logger = logging.getLogger(__name__)

# Columns introduced by Alembic revisions that may be missing on older DBs
# when alembic_version was never created. Keep in sync with migrations/versions.
REQUIRED_POST_COLUMNS = (
    (
        "is_published_instagram",
        "ALTER TABLE posts ADD COLUMN is_published_instagram BOOLEAN DEFAULT FALSE",
        "UPDATE posts SET is_published_instagram = FALSE WHERE is_published_instagram IS NULL",
    ),
    (
        "published_instagram_at",
        "ALTER TABLE posts ADD COLUMN published_instagram_at TIMESTAMP",
        None,
    ),
    (
        "telegram_link",
        "ALTER TABLE posts ADD COLUMN telegram_link VARCHAR",
        None,
    ),
)

# Must match the tip of migrations/versions after all required columns exist.
ALEMBIC_HEAD_REVISION = "add_telegram_link_field"


def ensure_required_post_columns():
    """Add any missing Post columns that Alembic would have created."""
    try:
        inspector = inspect(engine)
        if "posts" not in inspector.get_table_names():
            logger.info("Таблица posts ещё не создана — колонки добавит create_all/Alembic")
            return False

        columns = {col["name"] for col in inspector.get_columns("posts")}
        added_any = False

        with engine.connect() as conn:
            for name, add_sql, backfill_sql in REQUIRED_POST_COLUMNS:
                if name in columns:
                    logger.info("Колонка %s уже существует", name)
                    continue
                logger.info("Колонка %s отсутствует. Добавляю...", name)
                conn.execute(text(add_sql))
                if backfill_sql:
                    conn.execute(text(backfill_sql))
                added_any = True
                logger.info("Колонка %s успешно добавлена", name)
            if added_any:
                conn.commit()

        return added_any
    except Exception as e:
        logger.error("Ошибка при проверке/добавлении колонок posts: %s", e)
        return False


def check_and_add_column_if_missing():
    """Обратная совместимость: раньше добавляли только telegram_link."""
    return ensure_required_post_columns()


def init_alembic_version_table():
    """Инициализирует таблицу alembic_version, если её нет.

    Важно: штампуем head только после того, как схема уже приведена к head
    через ensure_required_post_columns. Раньше штамп ставили на
    add_instagram_fields без применения миграции — колонки Instagram
    отсутствовали, а последующий alembic upgrade head ломался на telegram_link.
    """
    try:
        with engine.connect() as conn:
            result = conn.execute(
                text(
                    """
                SELECT EXISTS (
                    SELECT FROM information_schema.tables
                    WHERE table_name = 'alembic_version'
                )
            """
                )
            )
            exists = result.scalar()

            if not exists:
                logger.info("Таблица alembic_version отсутствует. Создаю...")
                conn.execute(
                    text(
                        """
                    CREATE TABLE alembic_version (
                        version_num VARCHAR(32) NOT NULL,
                        CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num)
                    )
                """
                    )
                )
                conn.execute(
                    text(
                        """
                    INSERT INTO alembic_version (version_num)
                    VALUES (:revision)
                """
                    ),
                    {"revision": ALEMBIC_HEAD_REVISION},
                )
                conn.commit()
                logger.info(
                    "Таблица alembic_version создана и помечена как %s",
                    ALEMBIC_HEAD_REVISION,
                )
                return True

            logger.info("Таблица alembic_version уже существует")
            return False
    except Exception as e:
        logger.error("Ошибка при инициализации alembic_version: %s", e)
        return False


def ensure_database_schema():
    """Обеспечивает актуальность схемы базы данных."""
    logger.info("Проверка схемы базы данных...")

    # Сначала колонки (в т.ч. Instagram), затем штамп Alembic на head.
    ensure_required_post_columns()
    init_alembic_version_table()

    logger.info("Проверка схемы базы данных завершена")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    ensure_database_schema()
