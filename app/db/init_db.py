import logging
from sqlalchemy.orm import Session

from app.db.database import SessionLocal, engine, Base
from app.api.models.post import Post, PublicationLog

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def init_db():
    """Initialize the database."""
    # Create tables
    Base.metadata.create_all(bind=engine)
    
    # Create session
    db = SessionLocal()
    
    try:
        # Check if tables exist
        if db.query(Post).first() is None:
            logger.info("Database initialized successfully")
        else:
            logger.info("Database already contains data")
    finally:
        db.close()

if __name__ == "__main__":
    logger.info("Creating initial data")
    init_db()
    logger.info("Initial data created")
