"""发送结果确认。

**这里为什么这么绕**：抖音渲染"自己发出的消息气泡"是在发送状态确定**之前**
的 —— 气泡先出现，旁边转个圈，之后才变成"发送成功"或者翻成红色的"重试"。
所以"看到气泡了"绝不能当成"发送成功"，否则任务日志全是绿的，火花却没续上。

判定必须走一个状态机：

    MATCHED
       |
       v
    OBSERVING_INITIAL   气泡刚出现，状态未定
       |  看到失败标记            -> FAILED
       |  看到转圈                -> WAITING_PENDING
       |  整个宽限窗口都干净      -> SUCCESS
       v
    WAITING_PENDING      发送中
       |  看到失败标记            -> FAILED
       |  转圈消失                -> STABILIZING
       v
    STABILIZING          转圈刚消失
       |  看到失败标记            -> FAILED
       |  转圈又出现              -> WAITING_PENDING
       |  连续稳定干净            -> SUCCESS

关键规则：**刚匹配到的气泡，即使当前干净，也只能算"未知"**。因为抖音是在
气泡出现之后才挂上转圈或重试标记的。必须连续观察一个完整的宽限窗口。
"""

from __future__ import annotations

import asyncio
import logging
import secrets
from typing import Any, Optional

from playwright.async_api import Locator, Page

from app.modules.douyin_spark import selectors
from app.modules.douyin_spark.chat_page import PageError, first_visible

logger = logging.getLogger("douyin-spark")

# 总预算：一条消息最多观察这么久
CONFIRM_TIMEOUT_MS = 20_000
# 轮询间隔
POLL_INTERVAL_MS = 300
# 首次干净宽限窗口：这段时间内一直干净才算成功
INITIAL_CLEAN_GRACE_MS = 2_500
# 转圈消失后需要保持干净的稳定窗口
STABLE_INTERVAL_MS = 600

ANCHOR_ATTR = "data-dsw-anchor"


async def _marker_visible(scope: Locator, selector_list: list[str]) -> bool:
    """scope 内部是否存在任一可见的标记节点。

    只在**单条消息**的范围内找，这样页面上其它无关的 loading 不会造成误判。
    """

    for selector in selector_list:
        try:
            marker = scope.locator(selector).first
            if await marker.count() and await marker.is_visible():
                return True
        except Exception:  # noqa: BLE001
            continue
    return False


async def mark_latest_outgoing(page: Page) -> tuple[str, str]:
    """给当前"最后一条自己发出的消息"打锚点，并记下它原来的内容。

    之后用锚点是否变化 / 内容是否变化来判断"新消息插进来了"。
    """

    anchor = secrets.token_hex(8)
    latest = await _latest_outgoing_locator(page)
    if latest is None:
        return anchor, ""

    try:
        content = latest.locator('[data-e2e="msg-item-content"]').first
        if await content.count():
            before_content = await content.inner_html()
        else:
            before_content = await latest.inner_html()
        # 注意 evaluate 只收一个 arg：属性名和值必须打包成数组传进去。
        # 早先这里写成 evaluate(expr, ANCHOR_ATTR, anchor)，锚点 token 被当成
        # timeout 参数，TypeError 被 except 吞掉，锚点从来没打上过。
        await latest.evaluate("(el, pair) => el.setAttribute(pair[0], pair[1])", [ANCHOR_ATTR, anchor])
    except Exception as exc:  # noqa: BLE001
        # 锚点失败会让"新消息是否出现"的判定退化成纯内容比对，必须让人看见
        logger.warning("打锚点失败，发送确认将退化为内容比对: %s", exc)
        return anchor, ""
    return anchor, before_content


async def _latest_outgoing_locator(page: Page) -> Optional[Locator]:
    for selector in selectors.get("latest_outgoing_message"):
        try:
            locator = page.locator(selector)
            if await locator.count():
                return locator.first
        except Exception:  # noqa: BLE001
            continue
    return None


async def clear_anchors(page: Page) -> None:
    """清掉所有锚点属性，避免残留。"""

    try:
        anchors = page.locator(f"[{ANCHOR_ATTR}]")
        await anchors.evaluate_all(
            "(els, name) => els.forEach(el => el.removeAttribute(name))",
            ANCHOR_ATTR,
        )
    except Exception:  # noqa: BLE001
        pass


async def await_terminal_state(
    page: Page,
    scope: Locator,
    label: str,
    *,
    timeout_ms: int = CONFIRM_TIMEOUT_MS,
) -> None:
    """等这条消息进入终态。成功就返回，失败/不确定就抛 :class:`PageError`。

    注意：**不确定（超时）也算失败**。宁可报错让人来看，也不要谎报成功。
    """

    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_ms / 1000
    failure_markers = selectors.get("send_failure_markers")
    pending_markers = selectors.get("send_pending_markers")

    # ── 阶段 1：刚出现的干净气泡 ────────────────────────────────────
    grace_deadline = loop.time() + INITIAL_CLEAN_GRACE_MS / 1000
    while loop.time() < grace_deadline:
        if loop.time() >= deadline:
            raise PageError(f"{label}发送状态未能确认（超时），为避免重复不会自动重试")
        if await _marker_visible(scope, failure_markers):
            raise PageError(f"{label}发送失败，页面提示可以重试")
        if await _marker_visible(scope, pending_markers):
            break  # 转到阶段 2
        await page.wait_for_timeout(POLL_INTERVAL_MS)
    else:
        # 整个宽限窗口都干净 —— 发送很快，直接算成功
        return

    # ── 阶段 2/3：转圈出现 → 等它消失 → 要求稳定干净 ────────────────
    while True:
        if loop.time() >= deadline:
            raise PageError(f"{label}发送状态未能确认（超时），为避免重复不会自动重试")
        if await _marker_visible(scope, failure_markers):
            raise PageError(f"{label}发送失败，页面提示可以重试")
        if not await _marker_visible(scope, pending_markers):
            # 转圈没了。要求它**保持**干净，因为重试标记可能晚一拍才挂上。
            await page.wait_for_timeout(STABLE_INTERVAL_MS)
            if await _marker_visible(scope, failure_markers):
                raise PageError(f"{label}发送失败，页面提示可以重试")
            if not await _marker_visible(scope, pending_markers):
                return  # 终态：成功
        await page.wait_for_timeout(POLL_INTERVAL_MS)


async def confirm_outgoing(
    page: Page,
    before: tuple[str, str],
    label: str,
    *,
    expected_text: str = "",
    resource_key: str = "",
    timeout_ms: int = CONFIRM_TIMEOUT_MS,
) -> None:
    """确认新消息已发出并进入终态。

    ``expected_text`` 用于文字消息（校验内容），``resource_key`` 用于表情/图片
    （校验资源 URL 里包含的资源标识）。
    """

    anchor, before_content = before
    try:
        await page.wait_for_function(
            """([selector, anchorAttr, anchor, previousContent, expectedResource, expectedText]) => {
                const message = document.querySelector(selector);
                if (!message) return false;
                const content = message.querySelector('[data-e2e="msg-item-content"]') || message;
                const isNew =
                    message.getAttribute(anchorAttr) !== anchor ||
                    content.innerHTML !== previousContent;
                if (!isNew) return false;
                const normalize = (v) => (v || '').replace(/[\\s\\u200B\\u200C\\u200D\\uFEFF]+/g, ' ').trim();
                if (expectedText) {
                    return normalize(content.innerText).includes(normalize(expectedText));
                }
                if (!expectedResource) return true;
                const images = [...content.querySelectorAll('img')];
                return images.some(img => (img.src || '').includes(expectedResource)) || images.length > 0;
            }""",
            arg=[
                selectors.get("latest_outgoing_message")[0],
                ANCHOR_ATTR,
                anchor,
                before_content,
                resource_key,
                expected_text,
            ],
            timeout=timeout_ms,
        )
        latest = await _latest_outgoing_locator(page)
        if latest is None:
            raise PageError(f"{label}已发送，但没有检测到新的已发送消息")
        await await_terminal_state(page, latest, label, timeout_ms=timeout_ms)
    except PageError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise PageError(f"{label}已发送，但没有检测到新的已发送消息") from exc
    finally:
        await clear_anchors(page)


async def click_retry_on_latest(page: Page) -> bool:
    """点一下失败消息旁的重试按钮。"""

    latest = await _latest_outgoing_locator(page)
    if latest is None:
        return False
    for selector in selectors.get("send_retry_markers"):
        try:
            marker = latest.locator(selector).first
            if await marker.count() and await marker.is_visible():
                await marker.click(force=True)
                return True
        except Exception:  # noqa: BLE001
            continue
    return False


async def publish_ready(page: Page) -> bool:
    """发送按钮是否可用（表情有时只是插入了输入框，还没发出去）。"""

    for selector in selectors.get("send_button"):
        try:
            candidate = page.locator(selector).first
            if await candidate.count() and await candidate.is_visible():
                return True
        except Exception:  # noqa: BLE001
            continue
    return False


async def trigger_send(page: Page) -> None:
    """触发发送。有按钮点按钮，没有就回车。"""

    for selector in selectors.get("send_button"):
        try:
            candidate = page.locator(selector).first
            if await candidate.count() and await candidate.is_visible():
                await candidate.click()
                return
        except Exception:  # noqa: BLE001
            continue
    await page.keyboard.press("Enter")
