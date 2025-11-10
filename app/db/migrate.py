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

def ensure_database_schema():
    """Обеспечивает актуальность схемы базы данных."""
    logger.info("Проверка схемы базы данных...")
    
    # Проверяем и добавляем колонку telegram_link
    check_and_add_column_if_missing()
    
    # Инициализируем alembic_version, если нужно
    init_alembic_version_table()
    
    logger.info("Проверка схемы базы данных завершена")

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    ensure_database_schema()

