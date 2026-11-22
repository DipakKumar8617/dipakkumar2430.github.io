from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

from backend.app import models  # noqa: F401
from backend.app.database import Base, engine


config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def migration_url_override():
    configured = config.get_main_option("sqlalchemy.url")
    ini_default = (
        config.file_config.get("alembic", "sqlalchemy.url", fallback=None)
        if config.config_file_name
        else None
    )
    return configured if configured and configured != ini_default else None


def migration_url():
    return migration_url_override() or engine.url.render_as_string(
        hide_password=True
    )


def run_migrations_offline() -> None:
    context.configure(
        url=migration_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    supplied_connection = config.attributes.get("connection")

    if supplied_connection is not None:
        context.configure(
            connection=supplied_connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()
        return

    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
