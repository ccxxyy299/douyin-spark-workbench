"""抖音续火花模块的 HTTP 接口。

路由只做参数校验和 HTTP 语义，实质逻辑在 :mod:`service` / :mod:`executor`。
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from app.db import get_session, session_scope
from app.models import Account, Friend, MessageTemplate, SendJob, SendLog, SendPlan, StickerItem, today_str
from app.modules.douyin_spark import selectors
from app.modules.douyin_spark.browser import browser_manager, wait_for_login
from app.modules.douyin_spark.chat_page import DouyinChatPage, first_visible
from app.modules.douyin_spark.executor import execute_job
from app.modules.douyin_spark.service import (
    bulk_set_selection,
    check_login,
    collect_stats,
    create_account,
    delete_account,
    delete_plan,
    delete_template,
    generate_jobs_for_plan,
    list_accounts,
    list_friends,
    list_plans,
    list_templates,
    prepare_quick_jobs,
    save_plan,
    save_template,
    sync_friends,
    update_friend,
)
from app.modules.douyin_spark.sticker_catalog import collect_sticker_catalog
from app.settings import ARTIFACTS_DIR, settings

logger = logging.getLogger("douyin-spark")

router = APIRouter(prefix="/api/douyin", tags=["douyin-spark"])


# ── 请求体 ───────────────────────────────────────────────────────────────
class AccountCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    note: str = ""


class FriendUpdate(BaseModel):
    selected: Optional[bool] = None
    group_tag: Optional[str] = None
    note: Optional[str] = None
    display_name: Optional[str] = None


class SelectionRequest(BaseModel):
    account_id: int
    friend_ids: list[int] = Field(default_factory=list)
    selected: bool = True


class TemplatePayload(BaseModel):
    name: str = ""
    kind: str = "text"
    content: str = ""
    sticker_name: str = ""
    sticker_category: str = ""
    sticker_fallback_index: Optional[int] = None
    sticker_preview: str = ""
    enabled: bool = True
    weight: int = 1


class PlanPayload(BaseModel):
    name: str = ""
    target_friend_ids: list[int] = Field(default_factory=list)
    template_ids: list[int] = Field(default_factory=list)
    daily_time: str = "09:30"
    jitter_minutes: int = 0
    days_of_week: str = "0,1,2,3,4,5,6"
    date_from: str = ""
    date_to: str = ""
    enabled: bool = True


class QuickSendRequest(BaseModel):
    account_id: int
    # 传空 = 好友页里勾选中的所有人
    friend_ids: list[int] = Field(default_factory=list)
    # 传空 = 所有启用的模板随机挑
    template_ids: list[int] = Field(default_factory=list)


# ── 账号 ─────────────────────────────────────────────────────────────────
@router.get("/accounts")
def get_accounts(session: Session = Depends(get_session)) -> list[dict[str, Any]]:
    return [account.model_dump() for account in list_accounts(session)]


@router.post("/accounts")
def post_account(payload: AccountCreate, session: Session = Depends(get_session)) -> dict[str, Any]:
    account = create_account(session, payload.name, payload.note)
    return account.model_dump()


@router.delete("/accounts/{account_id}")
def remove_account(account_id: int, session: Session = Depends(get_session)) -> dict[str, bool]:
    delete_account(session, account_id)
    return {"ok": True}


@router.get("/accounts/{account_id}/login-status")
async def get_login_status(account_id: int) -> dict[str, Any]:
    return await check_login(account_id)


@router.post("/accounts/{account_id}/login")
async def start_login(account_id: int, timeout_seconds: int = Query(default=300, ge=30, le=900)) -> dict[str, Any]:
    """打开一个有界面的浏览器让用户扫码登录。

    登录态落在该账号自己的 profile 目录里，登录一次以后长期有效。
    拿账号锁：登录期间该账号的任务必须让路 —— close_context 会把
    正在执行任务的页面直接关掉，不锁就会互相踩。
    """

    async with browser_manager.lock_for(account_id):
        await browser_manager.close_context(account_id)
        context = await browser_manager.context_for(account_id, headless=False)
        pages = context.pages
        page = pages[0] if pages else await context.new_page()
        try:
            await page.goto("https://www.douyin.com/", wait_until="domcontentloaded", timeout=settings.nav_timeout_ms)
        except Exception as exc:  # noqa: BLE001
            logger.warning("打开抖音首页失败: %s", exc)

        logged_in = await wait_for_login(context, timeout_seconds=timeout_seconds)

        if logged_in:
            with session_scope() as session:
                account = session.get(Account, account_id)
                if account is not None:
                    account.status = "active"
                    account.last_login_at = account.last_login_at or datetime.now()
                    session.add(account)

    return {"logged_in": logged_in}


@router.post("/accounts/{account_id}/logout")
async def logout(account_id: int) -> dict[str, bool]:
    await browser_manager.close_context(account_id)
    with session_scope() as session:
        account = session.get(Account, account_id)
        if account is not None:
            account.status = "unknown"
            session.add(account)
    return {"ok": True}


# ── 好友 ─────────────────────────────────────────────────────────────────
@router.get("/friends")
def get_friends(account_id: int, session: Session = Depends(get_session)) -> list[dict[str, Any]]:
    friends = list_friends(session, account_id)

    # "今天是否已续" 只看发送记录 —— 火花可能已经熄灭，用天数判断会误判
    today = today_str()
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

    result = []
    for friend in friends:
        item = friend.model_dump()
        item["sent_today"] = friend.id in sent_today
        result.append(item)
    return result


@router.post("/friends/sync")
async def post_friends_sync(account_id: int) -> dict[str, Any]:
    """刷新好友列表与火花天数。"""

    try:
        return await sync_friends(account_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.patch("/friends/{friend_id}")
def patch_friend(friend_id: int, payload: FriendUpdate, session: Session = Depends(get_session)) -> dict[str, Any]:
    friend = update_friend(session, friend_id, payload.model_dump(exclude_none=True))
    if friend is None:
        raise HTTPException(status_code=404, detail="好友不存在")
    return friend.model_dump()


@router.post("/friends/selection")
def post_selection(payload: SelectionRequest, session: Session = Depends(get_session)) -> dict[str, Any]:
    count = bulk_set_selection(session, payload.account_id, payload.friend_ids, payload.selected)
    return {"updated": count}


# ── 文案模板 ─────────────────────────────────────────────────────────────
@router.get("/templates")
def get_templates(account_id: int, session: Session = Depends(get_session)) -> list[dict[str, Any]]:
    return [template.model_dump() for template in list_templates(session, account_id)]


@router.post("/templates")
def post_template(account_id: int, payload: TemplatePayload, session: Session = Depends(get_session)) -> dict[str, Any]:
    template = save_template(session, account_id, payload.model_dump())
    return template.model_dump()


@router.put("/templates/{template_id}")
def put_template(template_id: int, payload: TemplatePayload, session: Session = Depends(get_session)) -> dict[str, Any]:
    template = save_template(session, 0, payload.model_dump(), template_id)
    if template is None:
        raise HTTPException(status_code=404, detail="模板不存在")
    return template.model_dump()


@router.delete("/templates/{template_id}")
def remove_template(template_id: int, session: Session = Depends(get_session)) -> dict[str, bool]:
    delete_template(session, template_id)
    return {"ok": True}


# ── 计划 ─────────────────────────────────────────────────────────────────
@router.get("/plans")
def get_plans(account_id: int, session: Session = Depends(get_session)) -> list[dict[str, Any]]:
    return [plan.model_dump() for plan in list_plans(session, account_id)]


@router.post("/plans")
def post_plan(account_id: int, payload: PlanPayload, session: Session = Depends(get_session)) -> dict[str, Any]:
    plan = save_plan(session, account_id, payload.model_dump())
    return plan.model_dump()


@router.put("/plans/{plan_id}")
def put_plan(plan_id: int, payload: PlanPayload, session: Session = Depends(get_session)) -> dict[str, Any]:
    plan = save_plan(session, 0, payload.model_dump(), plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="计划不存在")
    return plan.model_dump()


@router.delete("/plans/{plan_id}")
def remove_plan(plan_id: int, session: Session = Depends(get_session)) -> dict[str, bool]:
    delete_plan(session, plan_id)
    return {"ok": True}


@router.post("/plans/{plan_id}/generate")
def post_generate_jobs(plan_id: int, session: Session = Depends(get_session)) -> dict[str, Any]:
    """立刻为该计划生成今天的任务（不等到点）。"""

    plan = session.get(SendPlan, plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="计划不存在")
    created = generate_jobs_for_plan(plan_id, datetime.now())
    return {"created": created}


@router.post("/plans/{plan_id}/run-now")
async def post_run_now(plan_id: int) -> dict[str, Any]:
    """立刻生成并执行该计划的任务。"""

    with session_scope() as session:
        plan = session.get(SendPlan, plan_id)
        if plan is None:
            raise HTTPException(status_code=404, detail="计划不存在")
        account_id = plan.account_id
        plan_name = plan.name

    created = generate_jobs_for_plan(plan_id, datetime.now())
    executed = 0
    with session_scope() as session:
        jobs = session.exec(
            select(SendJob)
            .where(SendJob.plan_id == plan_id, SendJob.state == "pending")
            .order_by(SendJob.id)
        ).all()
        job_ids = [job.id for job in jobs if job.id is not None]

    for job_id in job_ids:
        await execute_job(job_id)
        executed += 1
        await asyncio.sleep(1.5)

    return {"created": created, "executed": executed, "plan": plan_name, "account_id": account_id}


@router.post("/send-now")
async def post_send_now(payload: QuickSendRequest) -> dict[str, Any]:
    """不走计划定时，立即给好友各发一条。

    仍走完整的执行链路：账号锁、当日上限、单好友当日一条、勿扰时段、
    发送终态确认 —— 和定时任务同一套保护。
    """

    with session_scope() as session:
        if session.get(Account, payload.account_id) is None:
            raise HTTPException(status_code=404, detail="账号不存在")

    plan_id, created = prepare_quick_jobs(payload.account_id, payload.friend_ids, payload.template_ids)

    executed = 0
    with session_scope() as session:
        jobs = session.exec(
            select(SendJob)
            .where(SendJob.plan_id == plan_id, SendJob.state == "pending")
            .order_by(SendJob.id)
        ).all()
        job_ids = [job.id for job in jobs if job.id is not None]

    for job_id in job_ids:
        await execute_job(job_id)
        executed += 1
        await asyncio.sleep(1.5)

    return {"created": created, "executed": executed}


# ── 表情采集 ─────────────────────────────────────────────────────────────
@router.post("/stickers/collect")
async def post_collect_stickers(account_id: int) -> dict[str, Any]:
    """打开表情面板，把里面能看见的表情采集出来。"""

    async with browser_manager.lock_for(account_id):
        page = await browser_manager.page_for(account_id)
        chat = DouyinChatPage(page)
        await chat.open()
        # 表情按钮挂在会话输入框的工具条上；不进会话就没有输入框。
        # 私信页刚打开时右侧往往是空的 —— 先进左侧第一个会话。
        if await first_visible(page, ["message_input"], timeout_ms=3_000) is None:
            if not await chat.open_first_conversation():
                raise HTTPException(
                    status_code=400,
                    detail="私信页里没有可进入的会话。表情面板必须先进入一个会话才能打开，"
                    "先在抖音里和任意好友产生过一次对话，再来采集",
                )
        try:
            items = await collect_sticker_catalog(page)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    with session_scope() as session:
        stored = 0
        for item in items:
            exists = session.exec(
                select(StickerItem).where(
                    StickerItem.account_id == account_id,
                    StickerItem.category == item.get("category", ""),
                    StickerItem.name == item.get("name", ""),
                )
            ).first()
            if exists is None:
                session.add(
                    StickerItem(
                        account_id=account_id,
                        category=item.get("category", ""),
                        name=item.get("name", ""),
                        fallback_index=item.get("fallback_index", 0),
                        preview=item.get("preview", ""),
                        resource_key=item.get("resource_key", ""),
                    )
                )
                stored += 1
            else:
                exists.fallback_index = item.get("fallback_index", exists.fallback_index)
                exists.preview = item.get("preview", exists.preview)
                session.add(exists)
        session.commit()

    return {"collected": len(items), "new": stored, "items": items}


@router.get("/stickers")
def get_stickers(account_id: int, session: Session = Depends(get_session)) -> list[dict[str, Any]]:
    items = session.exec(
        select(StickerItem)
        .where(StickerItem.account_id == account_id)
        .order_by(StickerItem.category, StickerItem.fallback_index)
    ).all()
    return [item.model_dump() for item in items]


@router.post("/templates/upload-image")
async def upload_image(file: UploadFile = File(...)) -> dict[str, Any]:
    """上传一张图片，存到 data/uploads 并返回绝对路径。

    图片消息需要的是**服务器本地路径**（Playwright 直接从磁盘读文件上传），
    所以必须先落到后端能访问的位置。
    """

    uploads_dir = settings.ARTIFACTS_DIR / "uploads"
    uploads_dir.mkdir(parents=True, exist_ok=True)

    original = Path(file.filename or "image.png")
    suffix = original.suffix.lower() or ".png"
    if suffix not in {".png", ".jpg", ".jpeg", ".gif", ".webp"}:
        raise HTTPException(status_code=400, detail="只支持 PNG / JPG / GIF / WEBP")

    target = uploads_dir / f"{uuid.uuid4().hex}{suffix}"
    content = await file.read()
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="图片超过 10MB")
    target.write_bytes(content)
    return {"path": str(target), "name": original.name, "size": len(content)}


# ── 任务与日志 ───────────────────────────────────────────────────────────
@router.get("/jobs")
def get_jobs(
    account_id: Optional[int] = None,
    state: Optional[str] = None,
    limit: int = Query(default=100, ge=1, le=1000),
    session: Session = Depends(get_session),
) -> list[dict[str, Any]]:
    statement = select(SendJob)
    if account_id is not None:
        statement = statement.where(SendJob.account_id == account_id)
    if state:
        statement = statement.where(SendJob.state == state)
    statement = statement.order_by(SendJob.id.desc()).limit(limit)
    return [job.model_dump() for job in session.exec(statement).all()]


@router.get("/logs")
def get_logs(
    account_id: Optional[int] = None,
    job_id: Optional[int] = None,
    limit: int = Query(default=200, ge=1, le=2000),
    session: Session = Depends(get_session),
) -> list[dict[str, Any]]:
    statement = select(SendLog)
    if account_id is not None:
        statement = statement.where(SendLog.account_id == account_id)
    if job_id is not None:
        statement = statement.where(SendLog.job_id == job_id)
    statement = statement.order_by(SendLog.id.desc()).limit(limit)
    return [log.model_dump() for log in session.exec(statement).all()]


@router.get("/stats")
def get_stats(account_id: Optional[int] = None, session: Session = Depends(get_session)) -> dict[str, Any]:
    return collect_stats(session, account_id)


@router.get("/selectors")
def get_selectors() -> dict[str, Any]:
    """当前生效的选择器。改版排查用。"""

    return {"selectors": selectors.as_dict(), "chat_url": settings.chat_url}


@router.get("/artifacts/{name}")
def get_artifact(name: str) -> FileResponse:
    """失败截图。只允许取 artifacts 目录下一层的文件名。"""

    if "/" in name or "\\" in name or ".." in name:
        raise HTTPException(status_code=404, detail="文件不存在")
    path = ARTIFACTS_DIR / name
    if not path.is_file():
        raise HTTPException(status_code=404, detail="文件不存在")
    return FileResponse(path)
