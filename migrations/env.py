from logging.config import fileConfig
import os
import sys

# Добавляем путь к проекту в PYTHONPATH
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
from sqlalchemy import create_engine, pool

from alembic import context

import app.db.register_models  # noqa: F401
from app.db.database import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _get_database_url() -> str:
    """URL из .env / DATABASE_URL приложения, не захардкоженный postgres:postgres из alembic.ini."""
    load_dotenv()
    url = os.getenv("DATABASE_URL", "").strip()
    if not url:
        from app.config.settings import DATABASE_URL as app_url
        url = (app_url or "").strip()
    if not url:
        url = config.get_main_option("sqlalchemy.url") or ""
    return url


def run_migrations_offline() -> None:
    url = _get_database_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = create_engine(_get_database_url(), poolclass=pool.NullPool)

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
