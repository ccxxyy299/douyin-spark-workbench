"""后台调度。

两个独立的循环：

* **plan_loop** —— 定期检查计划，为"今天应该跑"且"今天还没生成过"的计划生成任务
* **worker_loop** —— 定期领取到期的任务并执行

用"生成任务"和"执行任务"两步，而不是"到点直接执行"，是为了让所有状态都
落库可查：任务什么时候该发、实际什么时候发的、失败了没、前端随时能看。
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from sqlmodel import select

from app.db import session_scope
from app.models import SendPlan
from app.modules.douyin_spark.executor import run_pending_jobs
from app.modules.douyin_spark.service import generate_jobs_for_plan, plan_runs_on, purge_old_data
from app.settings import settings

logger = logging.getLogger("douyin-spark")

_tasks: list[asyncio.Task[None]] = []
_last_maintenance_date = ""


async def plan_loop() -> None:
    """计划生成循环。"""

    while True:
        try:
            await _generate_due_jobs()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - 循环不能因为一次异常就死掉
            logger.exception("生成计划任务时出错: %s", exc)
        await asyncio.sleep(settings.planner_interval_seconds)


async def _generate_due_jobs() -> None:
    global _last_maintenance_date
    today = datetime.now()
    plan_date = today.strftime("%Y-%m-%d")

    # 每天第一轮顺手做数据保留清理（日志 30 天 / 截图 7 天）
    if _last_maintenance_date != plan_date:
        _last_maintenance_date = plan_date
        try:
            removed = purge_old_data()
            if removed["logs"] or removed["screenshots"]:
                logger.info(
                    "例行清理：删除 %d 条过期日志、%d 张过期截图",
                    removed["logs"],
                    removed["screenshots"],
                )
        except Exception as exc:  # noqa: BLE001
            logger.exception("例行清理失败: %s", exc)

    with session_scope() as session:
        plans = session.exec(select(SendPlan).where(SendPlan.enabled == True)).all()  # noqa: E712
        # 出了这段 session 这些对象就是 detached 的，读字段会炸 —— 这里只把 id 带出去
        due_ids = [
            plan.id
            for plan in plans
            if plan_runs_on(plan, today) and plan.last_plan_date != plan_date
        ]

    for plan_id in due_ids:
        try:
            created = generate_jobs_for_plan(plan_id, today)
            if created:
                logger.info("计划 %s 生成了 %d 个任务", plan_id, created)
        except Exception as exc:  # noqa: BLE001
            logger.exception("计划 %s 生成任务失败: %s", plan_id, exc)


async def worker_loop() -> None:
    """任务执行循环。"""

    while True:
        try:
            count = await run_pending_jobs()
            if count:
                logger.info("本轮执行了 %d 个任务", count)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.exception("执行任务时出错: %s", exc)
        await asyncio.sleep(settings.job_poll_seconds)


def start() -> None:
    """启动调度（幂等）。"""

    if _tasks:
        return
    _tasks.append(asyncio.create_task(plan_loop(), name="dsw-plan-loop"))
    _tasks.append(asyncio.create_task(worker_loop(), name="dsw-worker-loop"))
    logger.info("调度已启动")


async def stop() -> None:
    """停止调度。"""

    for task in _tasks:
        task.cancel()
    for task in _tasks:
        try:
            await task
        except (asyncio.CancelledError, Exception):  # noqa: B014
            pass
    _tasks.clear()
    logger.info("调度已停止")
