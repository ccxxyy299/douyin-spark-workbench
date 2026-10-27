"""任务执行器。

一个任务 = 给某个好友发一条消息。执行链路：

    取任务 → 拿账号锁 → 限流闸门 → 打开私信页 → 进会话 → 发 → 确认 → 落库

三道保护：

1. **账号锁**：同一账号串行。抖音对"同号短时间多动作"很敏感。
2. **限流闸门**：当日总量、单个好友当日次数、勿扰时段。
3. **幂等**：(plan_id, friend_id, plan_date) 唯一，重启/改计划都不会重发。
"""

from __future__ import annotations

import asyncio
import logging
import random
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from sqlmodel import Session, select

from app.db import session_scope
from app.models import Account, Friend, MessageTemplate, SendJob, SendLog, today_str
from app.modules.douyin_spark.browser import browser_manager
from app.modules.douyin_spark.chat_page import DouyinChatPage, PageError
from app.modules.douyin_spark.senders import StickerRef, send_image, send_sticker, send_text
from app.settings import ARTIFACTS_DIR, settings

logger = logging.getLogger("douyin-spark")

# 单个任务的硬超时。页面/浏览器进程僵死时，部分 Playwright 调用
# （count / is_visible 等）没有自己的超时，会永远挂住 —— 串行 worker
# 被一个任务堵死，后面的任务永远轮不上。这里强制兜底。
JOB_HARD_TIMEOUT_SECONDS = 300


def _log(session: Session, job: Optional[SendJob], level: str, message: str, account_id: Optional[int] = None) -> None:
    session.add(
        SendLog(
            job_id=job.id if job else None,
            account_id=account_id if account_id is not None else (job.account_id if job else None),
            level=level,
            message=message,
        )
    )


def _in_quiet_hours(now: datetime) -> bool:
    """是否处于勿扰时段。支持跨零点的区间（比如 23:00 → 08:00）。"""

    if not settings.quiet_hours_enabled:
        return False
    try:
        start_h, start_m = (int(x) for x in settings.quiet_hours_start.split(":"))
        end_h, end_m = (int(x) for x in settings.quiet_hours_end.split(":"))
    except (AttributeError, ValueError):
        return False
    current = now.hour * 60 + now.minute
    start = start_h * 60 + start_m
    end = end_h * 60 + end_m
    if start <= end:
        return start <= current < end
    return current >= start or current < end


def _quiet_hours_end(now: datetime) -> datetime:
    """勿扰时段的下一个结束时刻（支持跨零点的区间）。"""

    end_h, end_m = (int(x) for x in settings.quiet_hours_end.split(":"))
    candidate = now.replace(hour=end_h, minute=end_m, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=1)
    return candidate


def sent_today_count(session: Session, account_id: int) -> int:
    today = today_str()
    jobs = session.exec(
        select(SendJob).where(SendJob.account_id == account_id, SendJob.plan_date == today)
    ).all()
    return sum(1 for job in jobs if job.state == "sent")


def friend_sent_today(session: Session, account_id: int, friend_id: int) -> bool:
    today = today_str()
    jobs = session.exec(
        select(SendJob).where(
            SendJob.account_id == account_id,
            SendJob.friend_id == friend_id,
            SendJob.plan_date == today,
            SendJob.state == "sent",
        )
    ).all()
    return bool(jobs)


async def capture_screenshot(page, job_id: int) -> str:
    """失败时留一张截图，方便事后判断是页面变了还是被风控了。"""

    try:
        ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
        path = ARTIFACTS_DIR / f"job-{job_id}-{int(datetime.now().timestamp())}.png"
        await page.screenshot(path=str(path), full_page=False)
        return str(path)
    except Exception as exc:  # noqa: BLE001
        logger.debug("截图失败: %s", exc)
        return ""


def _sticker_ref_for(job_id: int, template_id: Optional[int], name: str) -> StickerRef:
    """拼出表情引用。模板上存着分类和兜底序号，任务表里只有名字。"""

    if not template_id:
        return StickerRef(name=name)
    with session_scope() as session:
        template = session.get(MessageTemplate, template_id)
        if template is None:
            return StickerRef(name=name)
        return StickerRef(
            name=template.sticker_name or name,
            category=template.sticker_category,
            fallback_index=template.sticker_fallback_index,
        )


async def execute_job(job_id: int) -> None:
    """执行一个发送任务。异常一律吞掉并落库，不让 worker 崩掉。"""

    with session_scope() as session:
        job = session.get(SendJob, job_id)
        if job is None or job.state != "pending":
            return
        job.state = "running"
        job.attempt += 1
        job.updated_at = datetime.now()
        session.add(job)
        account_id = job.account_id
        friend_id = job.friend_id
        friend_name = job.friend_name
        message_kind = job.message_kind
        message_payload = job.message_payload
        template_id = job.template_id
        # 只把标量带出这段 session。session_scope 退出时会 commit + close，
        # 对象整体过期 —— 之后再读它的任何字段（连主键都一样）都会 DetachedInstanceError。

    page_holder: dict[str, Any] = {"page": None}

    async def _run() -> None:
        page = None
        # ── 限流闸门 ───────────────────────────────────────────────
        with session_scope() as session:
            if sent_today_count(session, account_id) >= settings.daily_send_cap:
                _skip(job_id, session, "已达当日发送上限")
                return
            if friend_sent_today(session, account_id, friend_id):
                _skip(job_id, session, "该好友今天已经发过了")
                return
            if _in_quiet_hours(datetime.now()):
                # 勿扰不是失败：推迟到时段结束再试，而不是永久跳过
                _defer(job_id, session, _quiet_hours_end(datetime.now()), "处于勿扰时段")
                return

        async with browser_manager.lock_for(account_id):
            page = await browser_manager.page_for(account_id)
            page_holder["page"] = page
            chat = DouyinChatPage(page)
            logger.info("job%s[%s]: 浏览器就绪，打开私信页", job_id, friend_name)
            try:
                await asyncio.wait_for(chat.open(), timeout=60)
            except asyncio.TimeoutError:
                raise PageError("打开私信页超时（60 秒），页面可能卡死")
            except PageError:
                raise
            except Exception as exc:  # noqa: BLE001
                raise PageError(f"打开私信页失败: {type(exc).__name__}: {exc}") from exc

            logger.info("job%s[%s]: 私信页就绪，查找会话", job_id, friend_name)
            try:
                await asyncio.wait_for(chat.open_conversation(friend_name), timeout=90)
            except asyncio.TimeoutError:
                raise PageError("查找/打开会话超时（90 秒），页面可能卡死")
            except PageError:
                raise
            except Exception as exc:  # noqa: BLE001
                raise PageError(f"打开会话失败: {type(exc).__name__}: {exc}") from exc

            logger.info("job%s[%s]: 会话已打开，读取火花徽标", job_id, friend_name)
            # 进会话后读一次平台上的真实火花徽标，回报给台账
            days, streak_text, flame_state = await chat.read_spark_days()

            logger.info("job%s[%s]: 发送 %s 消息", job_id, friend_name, message_kind)
            kind = message_kind
            if kind == "text":
                await send_text(page, message_payload)
            elif kind == "image":
                await send_image(page, message_payload)
            elif kind == "sticker":
                # 分类和兜底序号不在任务表里（message_payload 只有名字），
                # 从模板读 —— 抖音表情面板默认只渲染第一屏，
                # 不带分类就找不到不在默认分类里的表情。
                sticker = _sticker_ref_for(job_id, template_id, message_payload)
                await send_sticker(page, sticker)
            else:
                raise PageError(f"不支持的消息类型: {kind}")

            with session_scope() as session:
                current = session.get(SendJob, job_id)
                if current is None:
                    return
                current.state = "sent"
                current.actual_at = datetime.now()
                current.updated_at = datetime.now()
                current.error = ""
                session.add(current)

                friend = session.get(Friend, friend_id)
                if friend is not None:
                    friend.consecutive_failures = 0
                    friend.last_error = ""
                    if days:
                        friend.flame_days = days
                        friend.flame_text = streak_text
                        friend.flame_state = flame_state or friend.flame_state
                        friend.flame_updated_at = datetime.now()
                    friend.updated_at = datetime.now()
                    session.add(friend)

                _log(
                    session,
                    current,
                    "info",
                    f"已发送给「{friend_name}」"
                    + (f"（当前火花 {days} 天）" if days else ""),
                )

    try:
        await asyncio.wait_for(_run(), timeout=JOB_HARD_TIMEOUT_SECONDS)
    except PageError as exc:
        if "卡死" in str(exc):
            # 页面僵死后重试没有意义，直接换一个新浏览器
            try:
                await asyncio.wait_for(browser_manager.close_context(account_id), timeout=15)
            except Exception:  # noqa: BLE001
                pass
        await _handle_failure(job_id, page_holder["page"], str(exc), needs_human=exc.needs_human)
    except asyncio.TimeoutError:
        # 强制中止：顺手把这个账号的浏览器也重开，避免僵死的 context
        # 继续坑下一个任务
        try:
            await asyncio.wait_for(browser_manager.close_context(account_id), timeout=15)
        except Exception:  # noqa: BLE001
            pass
        await _handle_failure(
            job_id,
            page_holder["page"],
            f"任务执行超过 {JOB_HARD_TIMEOUT_SECONDS} 秒未完成，已强制中止并重开浏览器",
            needs_human=False,
        )
    except Exception as exc:  # noqa: BLE001
        await _handle_failure(job_id, page_holder["page"], f"{type(exc).__name__}: {exc}", needs_human=False)


def _skip(job_id: int, session: Session, reason: str) -> None:
    current = session.get(SendJob, job_id)
    if current is None:
        return
    current.state = "skipped"
    current.error = reason
    current.updated_at = datetime.now()
    session.add(current)
    _log(session, current, "info", f"跳过：{reason}")


def _defer(job_id: int, session: Session, until: datetime, reason: str) -> None:
    """推迟任务：保持 pending、到点再试。推迟不消耗重试次数。"""

    current = session.get(SendJob, job_id)
    if current is None:
        return
    current.state = "pending"
    current.plan_at = until
    current.attempt = max(0, current.attempt - 1)
    current.updated_at = datetime.now()
    session.add(current)
    _log(session, current, "info", f"推迟：{reason}，将于 {until:%m-%d %H:%M} 重试")


async def _handle_failure(job_id: int, page, message: str, *, needs_human: bool) -> None:
    """失败处理：需要人工的直接标 needs_human，其余按退避重试。"""

    screenshot = ""
    if page is not None:
        screenshot = await capture_screenshot(page, job_id)

    with session_scope() as session:
        current = session.get(SendJob, job_id)
        if current is None:
            return
        current.error = message[:500]
        current.screenshot = screenshot
        current.updated_at = datetime.now()

        if needs_human:
            current.state = "needs_human"
            # 登录失效/风控验证要人工处理，账号状态同步标失效，
            # 否则账号页一直绿着、任务却全在挂
            account = session.get(Account, current.account_id)
            if account is not None and account.status == "active":
                account.status = "expired"
                session.add(account)
        elif current.attempt <= settings.retry_limit:
            current.state = "pending"
            # 退避：5 分钟、15 分钟
            delay = timedelta(minutes=5 * (2 ** (current.attempt - 1)))
            current.plan_at = datetime.now() + delay
        else:
            current.state = "failed"

        session.add(current)

        friend = session.get(Friend, current.friend_id)
        if friend is not None:
            friend.consecutive_failures += 1
            friend.last_error = message[:200]
            friend.updated_at = datetime.now()
            session.add(friend)

        _log(session, current, "error", f"发送失败：{message[:300]}")


async def run_pending_jobs() -> int:
    """领取到期任务并执行。返回执行数量。

    串行执行：抖音风控对并发敏感，个人规模下串行完全够用。
    """

    with session_scope() as session:
        now = datetime.now()
        jobs = session.exec(
            select(SendJob)
            .where(SendJob.state == "pending", SendJob.plan_at <= now)
            .order_by(SendJob.plan_at)
            .limit(20)
        ).all()
        job_ids = [job.id for job in jobs if job.id is not None]

    for job_id in job_ids:
        await execute_job(job_id)
        # 两条之间随机停顿，避免连续请求的机器特征
        pause = random.uniform(
            settings.send_interval_min_seconds,
            settings.send_interval_max_seconds,
        )
        await asyncio.sleep(pause)

    return len(job_ids)
