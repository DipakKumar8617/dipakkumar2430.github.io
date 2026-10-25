import os

from sqlalchemy import URL, create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from sqlalchemy.pool import StaticPool

from .config import APP_ENV


def build_database_url():
    configured_url = os.getenv("DATABASE_URL", "").strip()
    if configured_url:
        return make_url(configured_url)

    database = os.getenv("MYSQL_DATABASE", "").strip()
    username = os.getenv("MYSQL_USER", "").strip()
    password = os.getenv("MYSQL_PASSWORD", "")

    if not database or not username or not password:
        raise RuntimeError(
            "Configure DATABASE_URL or MYSQL_DATABASE, MYSQL_USER, "
            "and MYSQL_PASSWORD before using the database."
        )

    host = os.getenv(
        "MYSQL_HOST",
        "127.0.0.1" if APP_ENV == "development" else "mysql",
    )
    port = int(os.getenv("MYSQL_PORT", "3306"))

    return URL.create(
        "mysql+pymysql",
        username=username,
        password=password,
        host=host,
        port=port,
        database=database,
        query={"charset": "utf8mb4"},
    )


DATABASE_URL = build_database_url()

engine_options = {"pool_pre_ping": True}
if DATABASE_URL.drivername.startswith("sqlite"):
    engine_options.pop("pool_pre_ping")
    engine_options["connect_args"] = {"check_same_thread": False}
    if DATABASE_URL.database in (None, "", ":memory:"):
        engine_options["poolclass"] = StaticPool

engine = create_engine(DATABASE_URL, **engine_options)

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
