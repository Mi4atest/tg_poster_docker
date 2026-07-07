import asyncio

from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

from app.config.settings import DATABASE_URL

# statement_timeout: зависший запрос отваливается быстро, а не держит соединение 30+ секунд
if DATABASE_URL.startswith("sqlite"):
    _connect_args = {"check_same_thread": False}
else:
    _connect_args = {"options": "-c statement_timeout=10000"}

# Create SQLAlchemy engine
engine = create_engine(
    DATABASE_URL,
    connect_args=_connect_args,
    pool_pre_ping=True,
    pool_recycle=300,
    pool_size=15,
    max_overflow=20,
    pool_timeout=30,
    pool_reset_on_return="rollback",
)

# Create session factory
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Create base class for models
Base = declarative_base()

# Dependency to get DB session
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


async def run_db(fn, *args, **kwargs):
    """Выполнить синхронную работу с БД в отдельном потоке (не блокирует event loop).

    fn — обычная sync-функция; сессию открывать внутри неё.
    """
    return await asyncio.to_thread(fn, *args, **kwargs)
