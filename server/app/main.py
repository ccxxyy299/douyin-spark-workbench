"""FastAPI 入口。

职责很薄：建表、挂各模块的路由、起停后台调度、把前端产物端起来。
业务逻辑一律留在模块内部，这里不放任何规则。
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator
from typing import Any

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlmodel import Session, select

from app.db import init_db, session_scope
from app.models import SendJob, SendLog, now
from app.modules import registry
from app.modules.douyin_spark import planner
from app.modules.douyin_spark.browser import browser_manager
from app.modules.douyin_spark.router import router as douyin_router
from app.settings import LOGS_DIR, WEB_DIST_DIR

logger = logging.getLogger("app")

DOUYIN_SPEC = registry.register(
    registry.ModuleSpec(
        id="douyin-spark",
        name="抖音续火花",
        description="定时给抖音好友发私信，维持聊天火花",
        icon="fire",
        prefix="/api/douyin",
        route_prefix="/douyin",
        nav=[
            {"key": "accounts", "label": "账号"},
            {"key": "friends", "label": "好友与火花"},
            {"key": "templates", "label": "文案与表情"},
            {"key": "plans", "label": "发送计划"},
            {"key": "jobs", "label": "任务记录"},
        ],
    )
)


def _configure_logging() -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    try:
        handlers.append(logging.FileHandler(LOGS_DIR / "server.log", encoding="utf-8"))
    except OSError:  # 日志目录不可写时不能拖垮启动
        pass
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
        handlers=handlers,
        force=True,
    )


def _recover_stale_jobs() -> int:
    """把上次进程被杀时留下的 running 任务放回 pending。

    调度循环只领取 pending 任务，不重置的话这些任务会永远卡在 running，
    当天对应的好友也就永远不会被重试。
    """

    with session_scope() as session:
        stale = session.exec(select(SendJob).where(SendJob.state == "running")).all()
        for job in stale:
            job.state = "pending"
            job.updated_at = now()
            session.add(job)
            session.add(
                SendLog(
                    job_id=job.id,
                    account_id=job.account_id,
                    level="warning",
                    message="上次运行时进程中断，任务已重新排队",
                )
            )
        return len(stale)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    _configure_logging()
    init_db()

    recovered = _recover_stale_jobs()
    if recovered:
        logger.warning("重置了 %d 个中断的 running 任务", recovered)

    planner.start()
    try:
        yield
    finally:
        await planner.stop()
        await browser_manager.shutdown()


app = FastAPI(title="个人工作台", version="0.1.0", lifespan=lifespan)
app.include_router(douyin_router)


@app.get("/api/modules")
def get_modules() -> list[dict[str, Any]]:
    """前端导航读这里 —— 加模块不用改前端。"""

    return [
        {
            "id": spec.id,
            "name": spec.name,
            "description": spec.description,
            "icon": spec.icon,
            "prefix": spec.prefix,
            "route_prefix": spec.route_prefix,
            "nav": spec.nav,
        }
        for spec in registry.all_modules()
    ]


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {"ok": True, "web_dist": WEB_DIST_DIR.is_dir()}


if WEB_DIST_DIR.is_dir():
    app.mount("/assets", StaticFiles(directory=WEB_DIST_DIR / "assets"), name="assets")

    @app.get("/{full_path:path}")
    def serve_spa(full_path: str):
        """单端口部署：静态文件直接给，其余路径交给前端路由。"""

        if full_path:
            candidate = (WEB_DIST_DIR / full_path).resolve()
            try:
                candidate.relative_to(WEB_DIST_DIR.resolve())
            except ValueError:
                # 路径逃出了 dist（比如 %2e%2e 穿越），一律回退到首页
                return FileResponse(WEB_DIST_DIR / "index.html")
            if candidate.is_file():
                return FileResponse(candidate)
        return FileResponse(WEB_DIST_DIR / "index.html")


def create_app() -> FastAPI:
    init_db()
    return app


if __name__ == "__main__":
    import uvicorn

    from app.settings import settings

    uvicorn.run("app.main:app", host=settings.host, port=settings.port, reload=False)
