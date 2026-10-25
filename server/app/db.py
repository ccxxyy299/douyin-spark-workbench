"""数据库会话管理。

单文件 SQLite，WAL 模式足够支撑单人使用。
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import event
from sqlmodel import Session, SQLModel, create_engine

from app.models import Account, Friend, MessageTemplate, SendJob, SendLog, SendPlan, StickerCategory, StickerItem
from app.settings import DB_PATH

engine = create_engine(
    f"sqlite:///{DB_PATH.as_posix()}",
    echo=False,
    connect_args={"check_same_thread": False},
)


@event.listens_for(engine, "connect")
def _sqlite_pragma(dbapi_connection, connection_record) -> None:
    """每个连接上启用 WAL / 忙等 / 外键。

    API 线程池和事件循环里的 planner/worker 会并发写库，
    默认 journal 模式下撞车就是 database is locked。
    """

    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.execute("PRAGMA foreign_keys=ON")
    finally:
        cursor.close()


def init_db() -> None:
    """建表 + 给老库补新增列。可重复调用。"""

    SQLModel.metadata.create_all(engine)
    _migrate()


def _migrate() -> None:
    """create_all 不会改已存在的表 —— 新增列在这里手工补。"""

    from sqlalchemy import text

    with engine.begin() as conn:
        friend_columns = {row[1] for row in conn.execute(text("PRAGMA table_info(friend)"))}
        if "flame_state" not in friend_columns:
            conn.execute(text("ALTER TABLE friend ADD COLUMN flame_state TEXT NOT NULL DEFAULT ''"))


@contextmanager
def session_scope() -> Iterator[Session]:
    """一个事务边界。正常提交，异常回滚。"""

    session = Session(engine)
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_session() -> Iterator[Session]:
    """FastAPI 依赖注入用。"""

    with Session(engine) as session:
        yield session


__all__ = [
    "engine",
    "init_db",
    "session_scope",
    "get_session",
    "Account",
    "Friend",
    "MessageTemplate",
    "SendPlan",
    "SendJob",
    "SendLog",
    "StickerCategory",
    "StickerItem",
]
