import logging
from sqlalchemy.orm import Session

import app.db.register_models  # noqa: F401
from app.db.database import SessionLocal, engine, Base
from app.api.models.post import Post, PublicationLog
from app.db.migrate import ensure_database_schema

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def init_db():
    """Initialize the database."""
    # Create tables
    Base.metadata.create_all(bind=engine)
    
    # Проверяем и применяем необходимые изменения схемы
    ensure_database_schema()
    
    logger.info("Database schema ready")

if __name__ == "__main__":
    logger.info("Creating initial data")
    init_db()
    logger.info("Initial data created")
