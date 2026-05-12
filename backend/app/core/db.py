import ssl
import contextlib
from typing import AsyncGenerator, Generator

from sqlmodel import create_engine, Session
from sqlalchemy import event
from sqlalchemy.orm import scoped_session, sessionmaker
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.config import settings


# TiDB Serverless clusters have a limitation: if there are no active connections for 5 minutes,
# they will shut down, which closes all connections, so we need to recycle the connections
# (``pool_recycle``).
#
# **Two sync engines (same DSN):**
# - ``engine``: ``autocommit=True`` — required by Celery tasks / admin scripts / ``Scoped_Session``
#   and other legacy call sites that import ``engine`` directly.
# - ``engine_transactional``: no driver autocommit — used only by ``get_db_session`` (FastAPI
#   ``SessionDep``) so ``flush`` / ``rollback`` / ``commit`` are one real DB transaction; pool is
#   smaller than ``engine`` (API concurrency vs. batch workers).
engine = create_engine(
    str(settings.SQLALCHEMY_DATABASE_URI),
    connect_args={"autocommit": True},
    pool_size=20,
    max_overflow=40,
    pool_recycle=300,
    pool_pre_ping=True,
)

engine_transactional = create_engine(
    str(settings.SQLALCHEMY_DATABASE_URI),
    # ~O(100) concurrent humans: only in-flight API requests hold connections; 10+20 is a
    # reasonable default; raise if TiDB wait_timeout / pool exhaustion appears under burst saves.
    pool_size=10,
    max_overflow=20,
    pool_recycle=300,
    pool_pre_ping=True,
)

# create a scoped session, ensure in multi-threading environment, each thread has its own session
Scoped_Session = scoped_session(sessionmaker(bind=engine, class_=Session))


def get_ssl_context():
    ssl_context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
    ssl_context.minimum_version = ssl.TLSVersion.TLSv1_2
    ssl_context.check_hostname = True
    return ssl_context


async_engine = create_async_engine(
    str(settings.SQLALCHEMY_ASYNC_DATABASE_URI),
    pool_recycle=300,
    connect_args={
        # seems config ssl in url is not working
        # we can only config ssl in connect_args
        "ssl": get_ssl_context(),
    }
    if settings.TIDB_SSL
    else {},
)


def prepare_db_connection(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    # In APTSELL.AI, we store datetime in the database using UTC timezone.
    # Therefore, we need to set the timezone to '+00:00'.
    cursor.execute("SET time_zone = '+00:00'")
    cursor.close()


event.listen(engine, "connect", prepare_db_connection)
event.listen(engine_transactional, "connect", prepare_db_connection)
event.listen(async_engine.sync_engine, "connect", prepare_db_connection)


def get_db_session() -> Generator[Session, None, None]:
    with Session(engine_transactional, expire_on_commit=False) as session:
        yield session


async def get_db_async_session() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSession(async_engine, expire_on_commit=False) as session:
        yield session


get_db_async_session_context = contextlib.asynccontextmanager(get_db_async_session)
