"""SQLite 数据模型。

设计要点：

* ``Friend.display_name`` 是好友的业务主键（抖音会话列表里显示的名字）。
  抖音没有稳定暴露 user_id，昵称是页面上唯一稳定的可见标识；``sec_uid``
  作为可选的加强键，能拿到就存。
* ``SendJob`` 上有 ``(plan_id, friend_id, plan_date)`` 唯一约束，
  这是"同一天同好友同计划只发一次"的落点 —— 重启、改计划、重复扫描
  都不会重复发送。
* 所有"今天是否已发"的判断都基于 ``SendJob.plan_date`` + 状态，
  不依赖火花天数（火花可能已经熄灭，用天数判断会误判）。
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Optional

from pydantic import NaiveDatetime
from sqlmodel import Field, SQLModel, UniqueConstraint


def now() -> datetime:
    return datetime.now()


def today_str() -> str:
    return datetime.now().strftime("%Y-%m-%d")


class Account(SQLModel, table=True):
    """一个抖音账号（一个独立的浏览器 profile）。"""

    __tablename__ = "account"

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True)
    note: str = ""
    profile_dir: str = ""
    # unknown | active | expired | busy
    status: str = Field(default="unknown")
    last_login_at: Optional[NaiveDatetime] = None
    last_check_at: Optional[NaiveDatetime] = None
    created_at: NaiveDatetime = Field(default_factory=now)


class Friend(SQLModel, table=True):
    """一个好友（会话）。

    注意：``display_name`` 是业务主键，同名好友会合并成一条 —— 这是
    抖音页面上没有稳定 user_id 时的已知取舍。
    """

    __tablename__ = "friend"
    __table_args__ = (UniqueConstraint("account_id", "display_name", name="uq_friend_account_name"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    account_id: int = Field(foreign_key="account.id", index=True)

    display_name: str = Field(index=True)
    sec_uid: str = Field(default="")
    nickname: str = Field(default="")

    # 最近一次从页面读到的火花天数（0 = 没读到 / 没有火花）
    flame_days: int = Field(default=0)
    flame_text: str = Field(default="")
    # 平台上徽标的渲染状态：normal（已续上，正常彩色）/ gray（灰色，还没续上或临期）
    # "" = 没读到徽标。这是扫描时的快照，不是实时值。
    flame_state: str = Field(default="")
    flame_updated_at: Optional[NaiveDatetime] = None

    # 用户是否勾选（勾选 = 需要续火花）
    selected: bool = Field(default=False)
    # 勾选顺序，数字越小越靠前
    selected_order: Optional[int] = None

    group_tag: str = Field(default="")
    note: str = Field(default="")

    has_conversation: bool = Field(default=True)
    consecutive_failures: int = Field(default=0)
    last_error: str = Field(default="")

    created_at: NaiveDatetime = Field(default_factory=now)
    updated_at: NaiveDatetime = Field(default_factory=now)


class MessageTemplate(SQLModel, table=True):
    """一条可复用的消息模板（文字 / 图片 / 抖音原生表情）。"""

    __tablename__ = "message_template"

    id: Optional[int] = Field(default=None, primary_key=True)
    account_id: int = Field(foreign_key="account.id", index=True)

    name: str = ""
    # text | image | sticker
    kind: str = Field(default="text")
    # 文字内容，或图片的绝对路径，或表情的 key
    content: str = ""
    # 抖音原生表情：面板里显示的名字
    sticker_name: str = Field(default="")
    sticker_category: str = Field(default="")
    sticker_fallback_index: Optional[int] = None
    # 表情缩略图（本地文件路径），供前端展示
    sticker_preview: str = Field(default="")

    enabled: bool = Field(default=True)
    weight: int = Field(default=1)

    created_at: NaiveDatetime = Field(default_factory=now)


class SendPlan(SQLModel, table=True):
    """一条定时发送计划。"""

    __tablename__ = "send_plan"

    id: Optional[int] = Field(default=None, primary_key=True)
    account_id: int = Field(foreign_key="account.id", index=True)
    name: str = ""

    # 目标：好友 id 列表（JSON）。为空表示"所有勾选中的好友"
    target_friend_ids: str = Field(default="[]")
    # 文案：模板 id 列表（JSON）。为空表示"所有启用的模板"
    template_ids: str = Field(default="[]")

    # "09:30"
    daily_time: str = Field(default="09:30")
    # 计划时间上的随机抖动分钟数（0 = 不抖动）
    jitter_minutes: int = Field(default=0)
    # 星期几，0=周一 ... 6=周日；"0,1,2,3,4,5,6" 表示每天
    days_of_week: str = Field(default="0,1,2,3,4,5,6")
    date_from: str = Field(default="")
    date_to: str = Field(default="")

    enabled: bool = Field(default=True)
    # 该计划最后一次生成任务的日期，用于避免重复生成
    last_plan_date: str = Field(default="")

    created_at: NaiveDatetime = Field(default_factory=now)


class SendJob(SQLModel, table=True):
    """一次具体的发送任务。"""

    __tablename__ = "send_job"
    __table_args__ = (UniqueConstraint("plan_id", "friend_id", "plan_date", name="uq_job_plan_friend_date"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    plan_id: int = Field(foreign_key="send_plan.id", index=True)
    account_id: int = Field(foreign_key="account.id", index=True)
    friend_id: int = Field(foreign_key="friend.id", index=True)

    friend_name: str = Field(default="")
    template_id: Optional[int] = Field(default=None)
    # text | image | sticker
    message_kind: str = Field(default="text")
    # 渲染后的文字 / 图片路径 / 表情 key
    message_payload: str = Field(default="")
    # 渲染后的完整文字，供前端预览
    message_preview: str = Field(default="")

    plan_date: str = Field(default="", index=True)
    plan_at: NaiveDatetime = Field(default_factory=now)
    actual_at: Optional[NaiveDatetime] = None

    # pending | running | sent | failed | skipped | needs_human
    state: str = Field(default="pending", index=True)
    attempt: int = Field(default=0)
    error: str = Field(default="")
    screenshot: str = Field(default="")

    created_at: NaiveDatetime = Field(default_factory=now)
    updated_at: NaiveDatetime = Field(default_factory=now)


class SendLog(SQLModel, table=True):
    """任务日志。"""

    __tablename__ = "send_log"

    id: Optional[int] = Field(default=None, primary_key=True)
    job_id: Optional[int] = Field(default=None, index=True)
    account_id: Optional[int] = Field(default=None, index=True)
    level: str = Field(default="info")
    message: str = Field(default="")
    created_at: NaiveDatetime = Field(default_factory=now)


class StickerCategory(SQLModel, table=True):
    """抖音表情面板的一个分类（采集表情时落库）。"""

    __tablename__ = "sticker_category"

    id: Optional[int] = Field(default=None, primary_key=True)
    account_id: int = Field(foreign_key="account.id", index=True)
    name: str = Field(index=True)
    order_index: int = Field(default=0)
    created_at: NaiveDatetime = Field(default_factory=now)


class StickerItem(SQLModel, table=True):
    """抖音表情面板里的一个表情。"""

    __tablename__ = "sticker_item"
    __table_args__ = (UniqueConstraint("account_id", "category", "name", name="uq_sticker_account_cat_name"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    account_id: int = Field(foreign_key="account.id", index=True)
    category: str = Field(default="", index=True)
    name: str = Field(index=True)
    fallback_index: int = Field(default=0)
    preview: str = Field(default="")
    resource_key: str = Field(default="")
    created_at: NaiveDatetime = Field(default_factory=now)


def json_list(raw: str) -> list[Any]:
    """安全地把 JSON 文本列读成 list。"""

    if not raw:
        return []
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return []
    return value if isinstance(value, list) else []


def dump_json(value: list[Any]) -> str:
    return json.dumps(value, ensure_ascii=False)
