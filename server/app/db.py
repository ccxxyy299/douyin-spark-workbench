"""数据库会话管理。

单文件 SQLite，WAL 模式足够支撑单人使用。
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import event
from sqlmodel import Session, SQLModel, create_engine

from app.models import Account, Friend, MessageTemplate, SendJob, SendLog, SendPlan, StickerCategory, StickerItem
from app.settings import DB_PATH

logger = logging.getLogger("app")

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
        plan_columns = {row[1] for row in conn.execute(text("PRAGMA table_info(send_plan)"))}
        if "deleted" not in plan_columns:
            conn.execute(text("ALTER TABLE send_plan ADD COLUMN deleted INTEGER NOT NULL DEFAULT 0"))
        _rebuild_send_job_without_unique(conn)


def _rebuild_send_job_without_unique(conn) -> None:
    """老库的 send_job 带着 (plan_id, friend_id, plan_date) 唯一索引，
    失败重跑要生成**新的任务行**（旧行保留为历史），会被它挡住宿。

    SQLite 去不掉表级约束，只能重建表。列定义和外键从 PRAGMA 动态抄，
    不手写 DDL，防止和 models.py 漂移。没有该索引（新库）则什么都不做。
    """

    from sqlalchemy import text

    row = conn.execute(
        text(
            "SELECT 1 FROM sqlite_master WHERE type='index' AND tbl_name='send_job' "
            "AND (name = 'uq_job_plan_friend_date' OR name LIKE 'sqlite_autoindex_send_job%') "
            "LIMIT 1"
        )
    ).fetchone()
    if row is None:
        return

    cols = []
    for info in conn.execute(text("PRAGMA table_info(send_job)")):
        piece = f'"{info[1]}" {info[2]}'
        if info[3]:
            piece += " NOT NULL"
        if info[4] is not None:
            piece += f" DEFAULT {info[4]}"
        if info[5]:
            piece += " PRIMARY KEY"
        cols.append(piece)

    fks = []
    for fk in conn.execute(text("PRAGMA foreign_key_list(send_job)")):
        fks.append(f'FOREIGN KEY ("{fk[3]}") REFERENCES "{fk[2]}" ("{fk[4]}")')

    conn.execute(text("DROP TABLE IF EXISTS send_job_migrated"))
    conn.execute(text(f"CREATE TABLE send_job_migrated ({', '.join(cols + fks)})"))
    conn.execute(text("INSERT INTO send_job_migrated SELECT * FROM send_job"))
    conn.execute(text("DROP TABLE send_job"))
    conn.execute(text("ALTER TABLE send_job_migrated RENAME TO send_job"))
    # create_all 对已存在的表不会补索引，重建后手工建回来
    for name, column in (
        ("ix_send_job_plan_id", "plan_id"),
        ("ix_send_job_account_id", "account_id"),
        ("ix_send_job_friend_id", "friend_id"),
        ("ix_send_job_plan_date", "plan_date"),
        ("ix_send_job_state", "state"),
    ):
        conn.execute(text(f"CREATE INDEX IF NOT EXISTS {name} ON send_job ({column})"))
    logger.info("send_job 表已重建：移除 (plan_id, friend_id, plan_date) 唯一约束")


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
