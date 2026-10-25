"""业务逻辑层。

API 路由只做参数校验和 HTTP 语义，实质逻辑都在这里。
"""

from __future__ import annotations

import logging
import random
import re
from datetime import datetime, timedelta
from typing import Any, Optional

from sqlalchemy import case
from sqlmodel import Session, select

from app.db import session_scope
from app.models import (
    Account,
    Friend,
    MessageTemplate,
    SendJob,
    SendLog,
    SendPlan,
    StickerCategory,
    StickerItem,
    dump_json,
    json_list,
    now,
    today_str,
)
from app.modules.douyin_spark.browser import browser_manager
from app.modules.douyin_spark.chat_page import (
    DouyinChatPage,
    PageError,
    classify_flame_state,
    normalize_name,
    parse_streak,
)
from app.settings import ARTIFACTS_DIR, profile_dir_for

logger = logging.getLogger("douyin-spark")

WEEKDAY_NAMES = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")


# ── 账号 ─────────────────────────────────────────────────────────────────
def list_accounts(session: Session) -> list[Account]:
    return list(session.exec(select(Account).order_by(Account.id)).all())


def create_account(session: Session, name: str, note: str = "") -> Account:
    account = Account(name=name.strip(), note=note.strip())
    session.add(account)
    session.commit()
    session.refresh(account)
    account.profile_dir = str(profile_dir_for(account.id))
    session.add(account)
    session.commit()
    session.refresh(account)
    return account


def delete_account(session: Session, account_id: int) -> None:
    # 顺序 = 先删子表再删账号（数据库开着外键约束）
    for model in (SendJob, SendLog, SendPlan, MessageTemplate, StickerItem, StickerCategory, Friend):
        rows = session.exec(select(model).where(model.account_id == account_id)).all()
        for row in rows:
            session.delete(row)
    row = session.get(Account, account_id)
    if row is not None:
        session.delete(row)
    session.commit()


async def check_login(account_id: int) -> dict[str, Any]:
    """检查登录态，顺带更新账号状态。

    必须拿账号锁：不拿的话，和正在执行的任务并发启动浏览器，
    同一个 profile 目录会被 launch 两次（Chrome 进程锁冲突）。
    """

    try:
        async with browser_manager.lock_for(account_id):
            context = await browser_manager.context_for(account_id)
            cookies = await context.cookies()
            names = {cookie.get("name", "") for cookie in cookies}
            from app.modules.douyin_spark import selectors

            logged_in = bool(names & set(selectors.get("logged_in_cookies")))
    except Exception as exc:  # noqa: BLE001
        return {"logged_in": False, "error": str(exc)}

    with session_scope() as session:
        account = session.get(Account, account_id)
        if account is not None:
            account.status = "active" if logged_in else "expired"
            account.last_check_at = now()
            session.add(account)

    return {"logged_in": logged_in, "error": ""}


# ── 好友 ─────────────────────────────────────────────────────────────────
def _sec_uid_from_href(href: str) -> str:
    """从会话条目里的用户链接提取 sec_uid（形如 /user/MS4wLjAB…）。"""

    match = re.search(r"/user/([A-Za-z0-9_-]+)", href)
    return match.group(1) if match else ""


def list_friends(session: Session, account_id: int) -> list[Friend]:
    """默认排序跟抖音一致：

    1. 正常火花，按天数降序
    2. 重燃中（火花断了正在救）—— 在正常火花下面
    3. 没有火花

    灰色/橙色只影响显示，不影响排序。
    """

    urgency = case(
        (Friend.flame_days > 0, 0),
        (Friend.flame_text.contains("燃"), 1),
        else_=2,
    )
    return list(
        session.exec(
            select(Friend)
            .where(Friend.account_id == account_id)
            .order_by(urgency, Friend.flame_days.desc(), Friend.selected.desc(), Friend.display_name)
        ).all()
    )


def update_friend(session: Session, friend_id: int, payload: dict[str, Any]) -> Optional[Friend]:
    friend = session.get(Friend, friend_id)
    if friend is None:
        return None
    for field in ("selected", "group_tag", "note", "display_name"):
        if field in payload and payload[field] is not None:
            setattr(friend, field, payload[field])
    if "selected" in payload and not payload["selected"]:
        friend.selected_order = None
    friend.updated_at = now()
    session.add(friend)
    session.commit()
    session.refresh(friend)
    return friend


def bulk_set_selection(session: Session, account_id: int, friend_ids: list[int], selected: bool) -> int:
    """批量勾选/取消勾选，并按传入顺序写 selected_order。"""

    count = 0
    for order, friend_id in enumerate(friend_ids):
        friend = session.get(Friend, friend_id)
        if friend is None or friend.account_id != account_id:
            continue
        friend.selected = selected
        friend.selected_order = order if selected else None
        friend.updated_at = now()
        session.add(friend)
        count += 1
    session.commit()
    return count


async def sync_friends(account_id: int) -> dict[str, Any]:
    """扫描抖音私信页，刷新好友列表与火花天数。

    刷新策略（保留用户的选择与分组）：
    * 新会话 → 新建 Friend，默认不勾选
    * 已有会话 → 只更新火花天数/昵称，**不动 selected**
    """

    async with browser_manager.lock_for(account_id):
        page = await browser_manager.page_for(account_id)
        chat = DouyinChatPage(page)
        await chat.open()
        conversations = await chat.scan_conversations()

    added = 0
    updated = 0
    today = today_str()

    with session_scope() as session:
        for item in conversations:
            display_name = normalize_name(item.get("name", ""))
            if not display_name:
                continue
            streak_text = (item.get("streak") or "").strip()
            days = parse_streak(streak_text)
            # 平台火花状态：从火焰图标 URL 判定（gray_* = 今天还没续，灰色）
            flame_state = classify_flame_state(item.get("streakIcon") or "")
            sec_uid = _sec_uid_from_href(item.get("href") or "")

            friend = session.exec(
                select(Friend).where(
                    Friend.account_id == account_id, Friend.display_name == display_name
                )
            ).first()

            if friend is None:
                friend = Friend(
                    account_id=account_id,
                    display_name=display_name,
                    sec_uid=sec_uid,
                    flame_days=days,
                    flame_text=streak_text,
                    flame_state=flame_state,
                    flame_updated_at=now() if days else None,
                    has_conversation=True,
                )
                added += 1
            else:
                if sec_uid:
                    friend.sec_uid = sec_uid
                friend.flame_days = days
                friend.flame_text = streak_text
                friend.flame_state = flame_state
                friend.flame_updated_at = now() if days else friend.flame_updated_at
                friend.has_conversation = True
                friend.updated_at = now()
                updated += 1
            session.add(friend)

        # 今天已发送过的，标出来给前端提示
        sent_today = {
            job.friend_id
            for job in session.exec(
                select(SendJob).where(
                    SendJob.account_id == account_id,
                    SendJob.plan_date == today,
                    SendJob.state == "sent",
                )
            ).all()
        }

    return {
        "total": len(conversations),
        "added": added,
        "updated": updated,
        "sent_today": sorted(sent_today),
    }


# ── 文案模板 ─────────────────────────────────────────────────────────────
def list_templates(session: Session, account_id: int) -> list[MessageTemplate]:
    return list(
        session.exec(
            select(MessageTemplate)
            .where(MessageTemplate.account_id == account_id)
            .order_by(MessageTemplate.id)
        ).all()
    )


def save_template(session: Session, account_id: int, payload: dict[str, Any], template_id: Optional[int] = None) -> Optional[MessageTemplate]:
    template: Optional[MessageTemplate] = None
    if template_id:
        template = session.get(MessageTemplate, template_id)
        if template is None:
            # 指定了 id 却不存在：返回 None 让路由报 404，
            # 不要用调用方传的 account_id=0 建孤儿记录
            return None
        # 已存在的模板归属它自己的账号，不要被调用方传的 0 覆盖掉
        account_id = template.account_id
    else:
        template = MessageTemplate(account_id=account_id)

    template.name = (payload.get("name") or "").strip()
    template.account_id = account_id
    template.kind = payload.get("kind") or "text"
    template.content = payload.get("content") or ""
    template.sticker_name = payload.get("sticker_name") or ""
    template.sticker_category = payload.get("sticker_category") or ""
    template.sticker_fallback_index = payload.get("sticker_fallback_index")
    template.sticker_preview = payload.get("sticker_preview") or ""
    template.enabled = bool(payload.get("enabled", True))
    template.weight = int(payload.get("weight") or 1)
    session.add(template)
    session.commit()
    session.refresh(template)
    return template


def delete_template(session: Session, template_id: int) -> None:
    template = session.get(MessageTemplate, template_id)
    if template is not None:
        session.delete(template)
        session.commit()


# ── 计划 ─────────────────────────────────────────────────────────────────
def list_plans(session: Session, account_id: int) -> list[SendPlan]:
    return list(
        session.exec(select(SendPlan).where(SendPlan.account_id == account_id).order_by(SendPlan.id)).all()
    )


def save_plan(session: Session, account_id: int, payload: dict[str, Any], plan_id: Optional[int] = None) -> Optional[SendPlan]:
    plan: Optional[SendPlan] = None
    if plan_id:
        plan = session.get(SendPlan, plan_id)
        if plan is None:
            # 同 save_template：不存在就 404，不建 account_id=0 的孤儿
            return None
        # 同理，已存在的计划保留原账号
        account_id = plan.account_id
    else:
        plan = SendPlan(account_id=account_id)

    plan.account_id = account_id
    plan.name = (payload.get("name") or "").strip()
    plan.target_friend_ids = dump_json(payload.get("target_friend_ids") or [])
    plan.template_ids = dump_json(payload.get("template_ids") or [])
    plan.daily_time = payload.get("daily_time") or "09:30"
    plan.jitter_minutes = int(payload.get("jitter_minutes") or 0)
    plan.days_of_week = payload.get("days_of_week") or "0,1,2,3,4,5,6"
    plan.date_from = payload.get("date_from") or ""
    plan.date_to = payload.get("date_to") or ""
    plan.enabled = bool(payload.get("enabled", True))

    if plan_id:
        # 编辑计划 = 当天的生成结果可能已经过时（改时间/加人/减人）。
        # 清掉"今天已生成"标记，让 planner 按新配置补生成缺的任务
        # （已发送/已跳过的旧任务不会重来）。
        plan.last_plan_date = ""
        # 显式目标里已经移除的好友：取消今天还没发出去的任务
        explicit_ids = set(json_list(plan.target_friend_ids))
        if explicit_ids:
            today = today_str()
            pendings = session.exec(
                select(SendJob).where(
                    SendJob.plan_id == plan.id,
                    SendJob.plan_date == today,
                    SendJob.state == "pending",
                )
            ).all()
            for job in pendings:
                if job.friend_id not in explicit_ids:
                    job.state = "skipped"
                    job.error = "计划已调整，该好友不在目标里了"
                    job.updated_at = now()
                    session.add(job)

    session.add(plan)
    session.commit()
    session.refresh(plan)
    return plan


def delete_plan(session: Session, plan_id: int) -> None:
    plan = session.get(SendPlan, plan_id)
    if plan is not None:
        session.delete(plan)
        session.commit()


def plan_runs_on(plan: SendPlan, day: datetime) -> bool:
    """这个计划在指定日期要不要跑。"""

    date_text = day.strftime("%Y-%m-%d")
    if plan.date_from and date_text < plan.date_from:
        return False
    if plan.date_to and date_text > plan.date_to:
        return False
    weekday = day.weekday()
    days = {int(x) for x in plan.days_of_week.split(",") if x.strip().isdigit()}
    if days and weekday not in days:
        return False
    return True


def plan_datetime(plan: SendPlan, day: datetime) -> datetime:
    """计划在某天的具体执行时刻（含随机抖动）。"""

    try:
        hour, minute = (int(x) for x in plan.daily_time.split(":"))
    except (AttributeError, ValueError):
        hour, minute = 9, 30
    base = day.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if plan.jitter_minutes > 0:
        base += timedelta(minutes=random.randint(0, plan.jitter_minutes))
    return base


# ── 文案渲染 ─────────────────────────────────────────────────────────────
def render_text(template: MessageTemplate, friend: Friend, day: datetime) -> str:
    """渲染模板变量。

    支持 ``{昵称}`` ``{日期}`` ``{星期}`` ``{时间}``。
    """

    replacements = {
        "{昵称}": friend.display_name,
        "{日期}": day.strftime("%Y-%m-%d"),
        "{星期}": WEEKDAY_NAMES[day.weekday()],
        "{时间}": day.strftime("%H:%M"),
    }
    text = template.content or ""
    for token, value in replacements.items():
        text = text.replace(token, value)
    return text


def choose_templates(session: Session, plan: SendPlan) -> list[MessageTemplate]:
    """挑出这个计划可以用的模板。"""

    explicit = json_list(plan.template_ids)
    if explicit:
        templates = [session.get(MessageTemplate, tid) for tid in explicit]
        return [t for t in templates if t is not None and t.enabled]

    templates = list_templates(session, plan.account_id)
    return [t for t in templates if t.enabled]


def pick_template(templates: list[MessageTemplate], friend: Friend, session: Session) -> Optional[MessageTemplate]:
    """在候选模板里挑一条，尽量避开这个好友上一次用过的。"""

    if not templates:
        return None
    if len(templates) == 1:
        return templates[0]

    last = session.exec(
        select(SendJob)
        .where(SendJob.friend_id == friend.id, SendJob.template_id.is_not(None))
        .order_by(SendJob.id.desc())
    ).first()
    last_template_id = last.template_id if last else None

    candidates = [t for t in templates if t.id != last_template_id] or templates
    weights = [max(1, t.weight) for t in candidates]
    return random.choices(candidates, weights=weights, k=1)[0]


# ── 生成任务 ─────────────────────────────────────────────────────────────
def generate_jobs_for_plan(plan_id: int, day: datetime) -> int:
    """为某个计划在某天生成任务。

    幂等：``(plan_id, friend_id, plan_date)`` 唯一，重复调用不会重复生成。
    生成 0 条时**不**写 ``last_plan_date`` —— 当天晚些时候补勾好友 /
    启用模板，planner 还能补生成，不会静默漏发一整天。

    收 id 而不是收 SendPlan：调用方的 plan 属于另一个 session（甚至已经关闭），
    拿过来读属性会触发 DetachedInstanceError。
    """

    plan_date = day.strftime("%Y-%m-%d")
    created = 0

    with session_scope() as session:
        plan = session.get(SendPlan, plan_id)
        if plan is None:
            return 0

        target_ids = json_list(plan.target_friend_ids)
        if target_ids:
            # 显式指定的好友：只要还存在、属于这个账号就发。
            # "勾选"只约束留空（=全部勾选好友）的情况 —— 计划里点名的人
            # 还要求先去好友页打勾，和前端文案是矛盾的。
            friends = [session.get(Friend, fid) for fid in target_ids]
            friends = [f for f in friends if f is not None and f.account_id == plan.account_id]
        else:
            friends = [f for f in list_friends(session, plan.account_id) if f.selected]

        templates = choose_templates(session, plan)

        plan_at = plan_datetime(plan, day)

        for friend in friends:
            exists = session.exec(
                select(SendJob).where(
                    SendJob.plan_id == plan.id,
                    SendJob.friend_id == friend.id,
                    SendJob.plan_date == plan_date,
                )
            ).first()
            if exists is not None:
                continue

            template = pick_template(templates, friend, session)
            if template is None:
                continue

            preview = ""
            payload = ""
            if template.kind == "text":
                payload = render_text(template, friend, day)
                preview = payload
            elif template.kind == "image":
                payload = template.content
                preview = f"[图片] {template.name}"
            elif template.kind == "sticker":
                payload = template.sticker_name or template.content
                preview = f"[表情] {payload}"

            job = SendJob(
                plan_id=plan.id,
                account_id=plan.account_id,
                friend_id=friend.id,
                friend_name=friend.display_name,
                template_id=template.id,
                message_kind=template.kind,
                message_payload=payload,
                message_preview=preview,
                plan_date=plan_date,
                plan_at=plan_at,
                state="pending",
            )
            session.add(job)
            created += 1

        # 只有真的生成了任务才占用"当天名额"，见 docstring
        if created:
            plan.last_plan_date = plan_date
            session.add(plan)

    return created


# ── 直接发送 ─────────────────────────────────────────────────────────────
QUICK_SEND_PLAN_NAME = "直接发送（手动）"


def prepare_quick_jobs(account_id: int, friend_ids: list[int], template_ids: list[int]) -> tuple[int, int]:
    """为「直接发送」准备今天的任务。

    不想每次都建计划、设时间 —— 这个入口把目标好友和模板挂到一个
    每账号专属的容器计划上（``enabled=False``，不参与定时调度），
    复用 ``generate_jobs_for_plan`` 的全部逻辑（模板挑选、当天防重）。
    两个列表传空 = 全部勾选好友 / 全部启用模板。

    返回 ``(plan_id, 新建任务数)``。
    """

    with session_scope() as session:
        plan = session.exec(
            select(SendPlan).where(
                SendPlan.account_id == account_id, SendPlan.name == QUICK_SEND_PLAN_NAME
            )
        ).first()
        if plan is None:
            plan = SendPlan(account_id=account_id, name=QUICK_SEND_PLAN_NAME, enabled=False)
            session.add(plan)
            session.commit()
            session.refresh(plan)
        plan.target_friend_ids = dump_json(friend_ids)
        plan.template_ids = dump_json(template_ids)
        session.add(plan)
        session.commit()
        plan_id = plan.id or 0

    created = generate_jobs_for_plan(plan_id, datetime.now())
    return plan_id, created


# ── 统计 ─────────────────────────────────────────────────────────────────
def collect_stats(session: Session, account_id: Optional[int] = None) -> dict[str, Any]:
    today = today_str()

    def _scope(statement):
        if account_id is None:
            return statement
        return statement.where(SendJob.account_id == account_id)

    today_jobs = session.exec(
        _scope(select(SendJob).where(SendJob.plan_date == today))
    ).all()

    by_state: dict[str, int] = {}
    for job in today_jobs:
        by_state[job.state] = by_state.get(job.state, 0) + 1

    friends = session.exec(select(Friend)).all()
    if account_id is not None:
        friends = [f for f in friends if f.account_id == account_id]

    return {
        "today": today,
        "total_jobs_today": len(today_jobs),
        "by_state": by_state,
        "sent_today": by_state.get("sent", 0),
        "friends_total": len(friends),
        "friends_selected": sum(1 for f in friends if f.selected),
        "friends_with_flame": sum(1 for f in friends if f.flame_days > 0),
        "daily_cap": settings_daily_cap(),
    }


def settings_daily_cap() -> int:
    from app.settings import settings

    return settings.daily_send_cap


# ── 数据保留 ─────────────────────────────────────────────────────────────
def purge_old_data(log_days: int = 30, screenshot_days: int = 7) -> dict[str, int]:
    """清理过期日志和失败截图。

    挂机工具会天天写日志、失败就拍截图，没有任何保留策略的话
    SQLite 和 artifacts 目录会无限膨胀。planner 每天调一次。
    """

    log_cutoff = now() - timedelta(days=log_days)
    with session_scope() as session:
        old_logs = session.exec(select(SendLog).where(SendLog.created_at < log_cutoff)).all()
        for entry in old_logs:
            session.delete(entry)
    removed_logs = len(old_logs)

    removed_shots = 0
    shot_cutoff = datetime.now() - timedelta(days=screenshot_days)
    if ARTIFACTS_DIR.is_dir():
        for path in ARTIFACTS_DIR.glob("job-*.png"):
            try:
                if datetime.fromtimestamp(path.stat().st_mtime) < shot_cutoff:
                    path.unlink()
                    removed_shots += 1
            except OSError:
                continue

    return {"logs": removed_logs, "screenshots": removed_shots}
