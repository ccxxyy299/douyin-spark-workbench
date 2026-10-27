"""抖音私信页的页面对象。

这一层只关心"页面现在是什么状态"，不关心业务。
所有 DOM 定位都走 :mod:`selectors`，改版时只改那一处。
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, Optional

from playwright.async_api import Locator, Page

from app.modules.douyin_spark import selectors
from app.settings import settings

logger = logging.getLogger("douyin-spark")

# 火花文案形如 "🔥 81" / "81" / "81天"
_STREAK_NUMBER = re.compile(r"\d+")


class PageError(RuntimeError):
    """页面操作失败。``needs_human=True`` 表示需要人工介入（登录/验证）。"""

    def __init__(self, message: str, *, needs_human: bool = False) -> None:
        super().__init__(message)
        self.needs_human = needs_human


def parse_streak(text: str) -> int:
    """从火花文本里提取天数，解析不出来就当 0。

    "重燃中 2/3" 这类文案不是天数（火花已断、正在复燃），
    提取出来的数字没有意义，按 0 处理 —— 展示层显示原文。
    """

    value = str(text or "")
    if "重燃" in value or "复燃" in value:
        return 0
    match = _STREAK_NUMBER.search(value)
    return int(match.group()) if match else 0


def classify_flame_state(icon_src: str) -> str:
    """从火焰图标的 URL 里判定火花状态：gray（今天还没续）/ normal。

    实测（2026-10）抖音把状态编在图标 URL 里：
    ``.../flame_icon/<款式>/<颜色>_<款式>.png``，如
    ``normal/gray_normal.png``（灰色，没续）、款式还有 mate / soulmate。
    数字文本节点所有状态完全相同，类名和颜色都不可判 —— 只有这个 URL 可靠。
    """

    src = str(icon_src or "")
    if "/flame_icon/" not in src:
        return ""
    filename = src.rsplit("/", 1)[-1].lower()
    return "gray" if filename.startswith("gray_") else "normal"


def strip_flame_suffix(title: str) -> str:
    """剥掉会话标题里的火花状态尾巴，只留昵称。

    实测标题形如 ``名字 708`` / ``名字 重燃中 2/3`` / ``名字 6 天后消失``。

    **必须只剥"空格 + 状态"**：昵称本身可能以数字结尾（如"小甜甜123"），
    无条件剥数字会把名字截断，这类好友就永远确认不了会话、发不出去。
    """

    text = str(title or "").strip()
    previous = None
    while text and text != previous:
        previous = text
        text = re.sub(r"\s+\d+\s*天后消失$", "", text).strip()
        text = re.sub(r"\s+(重燃中|复燃)[\d\s/]*$", "", text).strip()
        text = re.sub(r"\s+\d+$", "", text).strip()
    return text


def normalize_name(value: str) -> str:
    """归一化显示名。

    抖音在某些位置会用不间断空格（U+00A0）和零宽字符（U+200B-D, U+FEFF），
    直接比对会漏。与 verifier.py 里的 JS normalize 保持同一语义。
    """

    import re

    text = str(value or "")
    text = re.sub(r"[\s\u00A0\u200B\u200C\u200D\uFEFF]+", " ", text)
    return text.strip()


async def first_visible(page: Page, selector_names: list[str], timeout_ms: int = 8_000) -> Optional[Locator]:
    """在若干候选选择器里找第一个「可见且非空」的节点。

    ``selector_names`` 是 :mod:`selectors` 里的键名列表。
    """

    deadline = asyncio.get_running_loop().time() + timeout_ms / 1000
    while True:
        for name in selector_names:
            for selector in selectors.get(name):
                try:
                    candidate = page.locator(selector)
                    if await candidate.count() == 0:
                        continue
                    locator = candidate.first
                    if await locator.is_visible():
                        return locator
                except Exception:  # noqa: BLE001 - 单个选择器失败就换下一个
                    continue
        if asyncio.get_running_loop().time() >= deadline:
            return None
        await asyncio.sleep(0.25)


class DouyinChatPage:
    """``https://www.douyin.com/chat`` 的封装。"""

    def __init__(self, page: Page) -> None:
        self.page = page
        self._conversation_name: str = ""

    # ── 打开 & 就绪检查 ───────────────────────────────────────────────
    async def open(self) -> None:
        """打开私信页并等待列表渲染出来。"""

        await self.page.goto(
            settings.chat_url,
            wait_until="domcontentloaded",
            timeout=settings.nav_timeout_ms,
        )
        await self._wait_for_ready()

    async def _wait_for_ready(self, timeout_ms: int = 30_000) -> None:
        """等待页面进入可用状态，并区分"需要登录"和"被风控"。"""

        deadline = asyncio.get_running_loop().time() + timeout_ms / 1000
        while asyncio.get_running_loop().time() < deadline:
            if await self.risk_detected():
                raise PageError("页面出现安全验证 / 操作频繁提示，需要人工处理", needs_human=True)
            if await self.logged_in():
                if await first_visible(self.page, ["conversation_title"], timeout_ms=1_000) is not None:
                    return
            elif await self.login_required():
                raise PageError("登录态已失效，需要重新扫码登录", needs_human=True)
            await asyncio.sleep(0.5)
        raise PageError("私信页在超时时间内没有渲染出会话列表")

    async def logged_in(self) -> bool:
        """靠 cookie 判断登录态。

        比在 DOM 里找"扫码登录"文案可靠得多 —— 文案会随改版变，cookie 不会。
        """

        try:
            cookies = await self.page.context.cookies()
        except Exception:  # noqa: BLE001
            return False
        names = {cookie.get("name", "") for cookie in cookies}
        wanted = set(selectors.get("logged_in_cookies"))
        return bool(names & wanted)

    async def login_required(self) -> bool:
        """页面上出现"扫码登录"之类的字样。"""

        return await self._any_text_visible(selectors.get("login_required"))

    async def risk_detected(self) -> bool:
        """页面上出现安全验证 / 操作频繁。"""

        return await self._any_text_visible(selectors.get("risk_markers"))

    async def _any_text_visible(self, texts: list[str]) -> bool:
        for text in texts:
            try:
                locator = self.page.locator(text)
                if await locator.count() and await locator.first.is_visible():
                    return True
            except Exception:  # noqa: BLE001
                continue
        return False

    # ── 会话列表 & 火花天数 ───────────────────────────────────────────
    async def scan_conversations(self, *, max_rounds: int = 40) -> list[dict[str, Any]]:
        """滚动会话列表，采集「好友名 + 火花天数」。

        抖音的会话列表是虚拟滚动的：滚下去时上方的 DOM 会被回收，所以不能
        "滚到底再一次性读"，必须边滚边收集。这里每轮读一次全量、和已有结果
        合并，连续两轮没有新增就认为到底了。
        """

        collected: dict[str, dict[str, Any]] = {}
        stable_rounds = 0

        async def harvest() -> int:
            """把当前渲染出来的条目合并进结果，返回新增条数。"""

            added = 0
            for item in await self._extract_conversations():
                key = normalize_name(item.get("name", ""))
                if not key:
                    continue
                previous = collected.get(key)
                if previous is None:
                    collected[key] = item
                    added += 1
                elif not previous.get("streak") and item.get("streak"):
                    # 后出现的条目可能带着火花徽标（首次渲染还没拿到）
                    collected[key] = item
            return added

        for _ in range(max_rounds):
            await harvest()

            if await self._reached_bottom():
                break

            await self._scroll_list()
            await asyncio.sleep(0.9)

            if await harvest() == 0:
                stable_rounds += 1
                if stable_rounds >= 2:
                    break
            else:
                stable_rounds = 0

        return list(collected.values())

    async def _extract_conversations(self) -> list[dict[str, Any]]:
        """在页面里执行一次采集。

        用页面内 JS 一次读出所有条目，比逐条走 Playwright locator 快一个量级。

        选择器必须从 :mod:`selectors` 传进去 —— 早先这里是把类名写死在 JS 里的，
        那样 ``selectors.yaml`` 的覆盖对扫描完全无效，改版时这一处就成了漏网点。
        """

        js = """
        ([titleSelectors, badgeSelectors, itemSelectors]) => {
            const pick = (root, list) => {
                for (const selector of list) {
                    try {
                        const found = root.querySelector(selector);
                        if (found) return found;
                    } catch (e) { /* 非法选择器跳过 */ }
                }
                return null;
            };
            const enclosingItem = (node) => {
                for (const selector of itemSelectors) {
                    try {
                        const found = node.closest(selector);
                        if (found) return found;
                    } catch (e) { /* 非法选择器跳过 */ }
                }
                return node.parentElement;
            };

            const out = [];
            const seen = new Set();
            for (const selector of titleSelectors) {
                let titles = [];
                try {
                    titles = [...document.querySelectorAll(selector)];
                } catch (e) { continue; }
                for (const t of titles) {
                    const name = (t.textContent || '').trim();
                    if (!name || seen.has(name)) continue;
                    seen.add(name);
                    const wrap = enclosingItem(t);
                    const badge = wrap ? pick(wrap, badgeSelectors) : null;
                    const avatar = wrap ? wrap.querySelector('img') : null;
                    out.push({
                        name: name,
                        streak: badge ? (badge.textContent || '').trim() : '',
                        // 火花状态在火焰图标的 URL 里（gray_* = 今天没续），不在文本节点上
                        streakIcon: wrap
                            ? ((wrap.querySelector('img.commonStreakicon')
                                || wrap.querySelector('[class*="streakContainer"] img')
                                || {}).src || '')
                            : '',
                        avatar: avatar ? (avatar.getAttribute('src') || '') : '',
                        href: (() => {
                            const link = wrap ? wrap.querySelector('a[href*="/user/"]') : null;
                            return link ? (link.getAttribute('href') || '') : '';
                        })(),
                    });
                }
            }
            return out;
        }
        """
        try:
            data = await self.page.evaluate(
                js,
                [
                    selectors.get("conversation_title"),
                    selectors.get("streak_badge"),
                    selectors.get("conversation_item"),
                ],
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("采集会话列表失败: %s", exc)
            return []
        return [item for item in (data or []) if isinstance(item, dict)]

    async def _reached_bottom(self) -> bool:
        for selector in selectors.get("no_more"):
            try:
                locator = self.page.locator(selector)
                if await locator.count() and await locator.first.is_visible():
                    return True
            except Exception:  # noqa: BLE001
                continue
        return False

    async def _scroll_list(self) -> None:
        """把左侧会话列表往下滚一屏。

        优先对着列表容器元素本身滚（拿到它的位置再滚轮）；
        容器定位不到时才退回固定的屏幕坐标。
        """

        for selector in selectors.get("conversation_list"):
            try:
                container = self.page.locator(selector).first
                if not await container.count():
                    continue
                box = await container.bounding_box()
                if box:
                    x = box["x"] + box["width"] / 2
                    y = box["y"] + min(box["height"] / 2, 400)
                    await self.page.mouse.move(x, y)
                    await self.page.mouse.wheel(0, 900)
                    return
            except Exception:  # noqa: BLE001
                continue
        try:
            await self.page.mouse.move(220, 380)
            await self.page.mouse.wheel(0, 900)
        except Exception:  # noqa: BLE001
            pass

    async def read_spark_days(self) -> tuple[int, str, str]:
        """读当前会话面板顶部的火花徽标。

        返回 ``(天数, 原始文案, 状态)``，状态见 :func:`classify_flame_state`。
        读不到就是 ``(0, "", "")``。
        """

        icon_src = ""
        icon = await first_visible(self.page, ["spark_icon"], timeout_ms=2_000)
        if icon is not None:
            try:
                icon_src = (await icon.get_attribute("src")) or ""
            except Exception:  # noqa: BLE001
                icon_src = ""

        locator = await first_visible(self.page, ["spark_status"], timeout_ms=3_000)
        if locator is None:
            return 0, "", classify_flame_state(icon_src)
        try:
            text = (await locator.inner_text()).strip()
        except Exception:  # noqa: BLE001
            return 0, "", classify_flame_state(icon_src)
        return parse_streak(text), text, classify_flame_state(icon_src)

    # ── 打开某个好友的会话 ────────────────────────────────────────────
    async def open_conversation(self, name: str, *, timeout_ms: int = 20_000) -> None:
        """通过搜索框找到好友并进入会话。

        优先用"搜索 + 点聊天按钮"，因为它不依赖好友在会话列表里的位置
        （列表很长时定位不可靠）。搜索找不到时回退到直接在列表里点。
        """

        target = normalize_name(name)
        if not target:
            raise PageError("好友名为空")

        if await self._open_via_search(target, timeout_ms=timeout_ms):
            return
        if await self._open_via_list(target, timeout_ms=timeout_ms):
            return
        raise PageError(f"没有找到好友「{name}」的会话")

    async def open_first_conversation(self, *, timeout_ms: int = 12_000) -> bool:
        """点开左侧列表里第一个可见的会话。

        表情采集这类操作依赖会话面板里的输入框 —— 不进会话就没有
        输入框，也就没有挂在输入框工具条上的表情按钮。
        """

        for title_selector in selectors.get("conversation_title"):
            try:
                titles = self.page.locator(title_selector)
                count = await titles.count()
            except Exception:  # noqa: BLE001
                continue
            for index in range(count):
                title = titles.nth(index)
                try:
                    if not await title.is_visible():
                        continue
                    await title.click()
                    if await first_visible(self.page, ["message_input"], timeout_ms=timeout_ms) is not None:
                        return True
                except Exception:  # noqa: BLE001
                    continue
        return False

    async def _open_via_search(self, name: str, *, timeout_ms: int) -> bool:
        search = await first_visible(self.page, ["search_input"], timeout_ms=5_000)
        if search is None:
            logger.debug("没有找到搜索框")
            return False

        try:
            await search.click()
            await search.fill("")
            await search.type(name, delay=40)
        except Exception as exc:  # noqa: BLE001
            logger.debug("搜索框输入失败: %s", exc)
            return False

        await asyncio.sleep(1.0)

        # 优先点条目里的"聊天"按钮
        for item_selector in selectors.get("search_item"):
            try:
                items = self.page.locator(item_selector)
                count = await items.count()
            except Exception:  # noqa: BLE001
                continue
            for index in range(min(count, 8)):
                item = items.nth(index)
                try:
                    if not await item.is_visible():
                        continue
                    if not await self._item_name_matches(item, name):
                        continue
                    button = None
                    for button_selector in selectors.get("chat_button"):
                        try:
                            candidate = item.locator(button_selector).first
                            if await candidate.count() and await candidate.is_visible():
                                button = candidate
                                break
                        except Exception:  # noqa: BLE001
                            continue
                    if button is not None:
                        await button.click()
                        if await self._confirm_conversation(name, timeout_ms=timeout_ms):
                            return True
                    # 没有"聊天"按钮就直接点条目
                    await item.click()
                    if await self._confirm_conversation(name, timeout_ms=timeout_ms):
                        return True
                except Exception:  # noqa: BLE001
                    continue
        # 没找到：清空搜索框并关掉下拉，别让它挡住后面列表路径的视线
        # （下拉开着时会话列表条目全部不可见，滚动也滚不动）
        try:
            await search.fill("")
            await self.page.keyboard.press("Escape")
            await asyncio.sleep(0.5)
        except Exception:  # noqa: BLE001
            pass
        return False

    async def _item_name_matches(self, item: Locator, name: str) -> bool:
        """判断搜索结果条目是不是目标好友（严格相等，避免发错人）。

        先试条目内部的名字子元素；子元素选择器失配时兜底用
        条目文本的第一行 —— 实测条目文本形如 ``"卞秋\\n重燃中 2/3\\n发消息"``，
        第一行就是昵称（2026-10 实测，重燃中的好友就是靠这条兜底命中的）。
        """

        for name_selector in selectors.get("search_item_name"):
            try:
                locator = item.locator(name_selector)
                if await locator.count() == 0:
                    continue
                text = normalize_name(await locator.first.inner_text())
                if text == name:
                    return True
            except Exception:  # noqa: BLE001
                continue
        try:
            first_line = normalize_name(await item.inner_text()).split("\n", 1)[0].strip()
            return first_line == name
        except Exception:  # noqa: BLE001
            return False

    async def _open_via_list(self, name: str, *, timeout_ms: int) -> bool:
        """回退方案：在左侧会话列表里找。

        列表是虚拟滚动的，只渲染第一屏 —— 好友排序沉底（比如重燃中、
        好久没聊的）就看不见。所以找不到就往下滚一屏再找，直到找到或到底。
        好友名本来就是从这张列表里读的，回这里匹配一定能对上。
        """

        deadline = asyncio.get_running_loop().time() + timeout_ms
        while asyncio.get_running_loop().time() < deadline:
            for title_selector in selectors.get("conversation_title"):
                try:
                    titles = self.page.locator(title_selector)
                    count = await titles.count()
                except Exception:  # noqa: BLE001
                    continue
                for index in range(count):
                    title = titles.nth(index)
                    try:
                        if not await title.is_visible():
                            continue
                        if normalize_name(await title.inner_text()) != name:
                            continue
                        await title.click()
                        if await self._confirm_conversation(name, timeout_ms=10_000):
                            return True
                        # 点了却没进对会话：继续往下找
                        break
                    except Exception:  # noqa: BLE001
                        continue
            if await self._reached_bottom():
                return False
            await self._scroll_list()
            await asyncio.sleep(0.8)
        return False

    async def _confirm_conversation(self, name: str, *, timeout_ms: int) -> bool:
        """确认会话面板确实切到了目标好友。"""

        deadline = asyncio.get_running_loop().time() + timeout_ms / 1000
        while asyncio.get_running_loop().time() < deadline:
            if await first_visible(self.page, ["message_input"], timeout_ms=800) is not None:
                title = await self.current_conversation_title()
                if title is None or normalize_name(title) == name:
                    self._conversation_name = name
                    return True
            await asyncio.sleep(0.3)
        return False

    async def current_conversation_title(self) -> Optional[str]:
        """当前会话面板顶部显示的名字（已剥掉火花状态尾巴）。"""

        for selector in selectors.get("conversation_panel_title"):
            try:
                locator = self.page.locator(selector)
                if await locator.count() == 0:
                    continue
                text = strip_flame_suffix(normalize_name(await locator.first.inner_text()))
                if text:
                    return text
            except Exception:  # noqa: BLE001
                continue
        return None

    # ── 输入框 ────────────────────────────────────────────────────────
    async def message_input(self) -> Locator:
        locator = await first_visible(self.page, ["message_input"], timeout_ms=10_000)
        if locator is None:
            raise PageError("找不到消息输入框")
        return locator
