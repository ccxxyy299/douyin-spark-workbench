"""浏览器会话管理。

核心决定：**用 ``launch_persistent_context`` 而不是 ``new_context(storage_state=...)``**。

persistent context 把 cookie / localStorage 直接落在账号自己的 profile 目录里，
所以"扫码登录一次"之后 forever 都不用再登 —— 而且每个账号一个独立目录，
抖音看不出"同一个浏览器里有多个号"。

登录态判定用 cookie 里的 ``sessionid``（比在 DOM 里找"扫码登录"文案可靠得多：
文案会随改版变，cookie 不会）。
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, Optional

from playwright.async_api import BrowserContext, Page, Playwright, async_playwright

from app.settings import profile_dir_for, settings

logger = logging.getLogger("douyin-spark")

# 常见的启动参数。尽量贴近真实浏览器，减少被识别成自动化的概率。
_LAUNCH_ARGS = [
    "--no-sandbox",
    "--disable-setuid-sandbox",
    "--disable-dev-shm-usage",
    "--disable-blink-features=AutomationControlled",
]

_DEFAULT_VIEWPORT = {"width": 1366, "height": 850}


class BrowserManager:
    """管理所有账号的 Playwright 上下文。

    单进程内共用一个 Playwright 实例，每个账号一个 context。
    """

    def __init__(self) -> None:
        self._playwright: Optional[Playwright] = None
        self._contexts: dict[int, BrowserContext] = {}
        self._locks: dict[int, asyncio.Lock] = {}
        self._pages: dict[int, Page] = {}

    # ── 生命周期 ───────────────────────────────────────────────────────
    async def start(self) -> None:
        if self._playwright is None:
            self._playwright = await async_playwright().start()
            logger.info("Playwright 已启动")

    async def shutdown(self) -> None:
        for account_id in list(self._contexts):
            await self.close_context(account_id)
        if self._playwright is not None:
            await self._playwright.stop()
            self._playwright = None
            logger.info("Playwright 已关闭")

    # ── 锁 ─────────────────────────────────────────────────────────────
    def lock_for(self, account_id: int) -> asyncio.Lock:
        """每账号一把锁：同一账号不允许并发操作。

        抖音对"同一账号短时间多动作"很敏感，串行是最低成本的防御。
        """

        if account_id not in self._locks:
            self._locks[account_id] = asyncio.Lock()
        return self._locks[account_id]

    # ── context ────────────────────────────────────────────────────────
    async def context_for(self, account_id: int, *, headless: Optional[bool] = None) -> BrowserContext:
        """拿到（必要时创建）某账号的浏览器上下文。"""

        await self.start()
        existing = self._contexts.get(account_id)
        if existing is not None:
            return existing

        profile = profile_dir_for(account_id)
        profile.mkdir(parents=True, exist_ok=True)

        launch_kwargs: dict[str, Any] = {
            "user_data_dir": str(profile),
            "headless": settings.headless if headless is None else headless,
            "viewport": _DEFAULT_VIEWPORT,
            "locale": "zh-CN",
            "timezone_id": "Asia/Shanghai",
            "args": _LAUNCH_ARGS,
        }
        if settings.browser_executable:
            launch_kwargs["executable_path"] = settings.browser_executable
        elif settings.browser_channel:
            launch_kwargs["channel"] = settings.browser_channel

        try:
            context = await self._playwright.chromium.launch_persistent_context(**launch_kwargs)
        except Exception as exc:  # noqa: BLE001
            # 本机没装 Chrome / Edge 时回退到 Playwright 自带的 chromium
            logger.warning("用 %s 启动失败（%s），回退到内置 chromium", launch_kwargs.get("channel", "chromium"), exc)
            fallback = {key: value for key, value in launch_kwargs.items() if key not in {"channel", "executable_path"}}
            context = await self._playwright.chromium.launch_persistent_context(**fallback)
        context.set_default_timeout(settings.nav_timeout_ms)
        context.set_default_navigation_timeout(settings.nav_timeout_ms)
        self._contexts[account_id] = context
        logger.info("账号 %s 的浏览器上下文已启动 (profile=%s)", account_id, profile)
        return context

    async def close_context(self, account_id: int) -> None:
        context = self._contexts.pop(account_id, None)
        self._pages.pop(account_id, None)
        if context is None:
            return
        try:
            await context.close()
        except Exception as exc:  # noqa: BLE001 - 关闭失败不应影响其它逻辑
            logger.warning("关闭账号 %s 的浏览器失败: %s", account_id, exc)

    async def page_for(self, account_id: int, *, headless: Optional[bool] = None) -> Page:
        """拿到该账号当前正在用的页面（没有就新开一个）。"""

        context = await self.context_for(account_id, headless=headless)
        page = self._pages.get(account_id)
        if page is not None and not page.is_closed():
            return page
        page = context.pages[0] if context.pages else await context.new_page()
        self._pages[account_id] = page
        return page

    async def reset_page(self, account_id: int) -> Page:
        """丢掉当前页面（新开一个），用于页面状态脏了的时候重来。"""

        context = await self.context_for(account_id)
        old = self._pages.get(account_id)
        if old is not None and not old.is_closed():
            try:
                await old.close()
            except Exception:  # noqa: BLE001
                pass
        page = await context.new_page()
        self._pages[account_id] = page
        return page


browser_manager = BrowserManager()


async def is_logged_in(context: BrowserContext) -> bool:
    """靠 cookie 判断登录态。"""

    try:
        cookies = await context.cookies()
    except Exception:  # noqa: BLE001
        return False
    names = {cookie.get("name", "") for cookie in cookies}
    from app.modules.douyin_spark import selectors

    wanted = set(selectors.get("logged_in_cookies"))
    return bool(names & wanted)


async def wait_for_login(context: BrowserContext, timeout_seconds: int = 300) -> bool:
    """轮询等待扫码登录完成。"""

    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while asyncio.get_running_loop().time() < deadline:
        if await is_logged_in(context):
            return True
        await asyncio.sleep(2)
    return False


def profile_path_for(account_id: int) -> Path:
    return profile_dir_for(account_id)
