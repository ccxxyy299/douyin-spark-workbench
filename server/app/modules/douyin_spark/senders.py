"""三种消息的发送实现。

* 文字 —— 一次性插入（不逐字打字），然后触发发送
* 图片 —— 走 ``input[type=file]`` 上传
* 抖音原生表情 —— 点开表情面板选表情

三者共同的坑：**"动作完成"不等于"发送成功"**。
文字和图片是"插入 + 发送"两步；表情更特殊 —— 点表情只是把表情放进了输入框，
抖音有时会直接发、有时要再点一次发送按钮。所以每个发送器最后都要走
:mod:`verifier` 的终态确认。
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from playwright.async_api import Locator, Page

from app.modules.douyin_spark import selectors
from app.modules.douyin_spark.chat_page import PageError, first_visible
from app.modules.douyin_spark.verifier import (
    ANCHOR_ATTR,
    await_terminal_state,
    click_retry_on_latest,
    confirm_outgoing,
    mark_latest_outgoing,
    publish_ready,
    trigger_send,
)

logger = logging.getLogger("douyin-spark")


@dataclass(frozen=True)
class StickerRef:
    """一个抖音原生表情的引用。

    ``name`` 是面板里显示的名字（用来定位），``category`` 是它所在的分类
    （面板默认只加载第一屏，不切分类就找不到），``fallback_index`` 是
    分类内的序号兜底。
    """

    name: str
    category: str = ""
    fallback_index: int | None = None


def _resource_key(src: str) -> str:
    """从图片 URL 里取出资源标识（最后一段路径），用来校验发出去的表情对不对。"""

    if not src:
        return ""
    return urlsplit(src).path.rsplit("/", 1)[-1]


async def send_text(page: Page, content: str) -> None:
    """发送文字消息。"""

    text = (content or "").strip()
    if not text:
        raise PageError("消息内容为空")

    editor = await first_visible(page, ["message_input"], timeout_ms=10_000)
    if editor is None:
        raise PageError("找不到消息输入框")

    await editor.click()
    # 一次性插入。逐字打字既慢又更像自动化。
    await page.keyboard.insert_text(text)

    # 确认文字真的进了输入框。候选必须和上面定位输入框用的是同一份
    # selectors.message_input —— 之前这里另写了一套类名，
    # 一旦真正匹配到的是别的候选，就会误报"未能写入输入框"。
    try:
        await page.wait_for_function(
            """([txt, editorSelectors]) => {
                for (const selector of editorSelectors) {
                    let nodes = [];
                    try { nodes = [...document.querySelectorAll(selector)]; } catch (e) { continue; }
                    if (nodes.some(e => (e.innerText || e.textContent || '').includes(txt))) return true;
                }
                return false;
            }""",
            arg=[text, selectors.get("message_input")],
            timeout=5_000,
        )
    except Exception as exc:  # noqa: BLE001
        raise PageError("文字未能写入聊天输入框") from exc

    before = await mark_latest_outgoing(page)
    await page.wait_for_timeout(300)
    await trigger_send(page)
    await confirm_outgoing(page, before, "文字", expected_text=text)


async def send_image(page: Page, image_path: str) -> None:
    """发送图片消息。"""

    path = Path(image_path)
    if not path.is_file():
        raise PageError(f"图片不存在: {image_path}")

    before = await mark_latest_outgoing(page)

    file_input: Locator | None = None
    for selector in selectors.get("image_input"):
        try:
            candidate = page.locator(selector).first
            if await candidate.count():
                file_input = candidate
                break
        except Exception:  # noqa: BLE001
            continue
    if file_input is None:
        raise PageError("找不到图片上传控件")

    await file_input.set_input_files(str(path))
    await page.wait_for_timeout(1_500)
    await trigger_send(page)

    try:
        await page.wait_for_function(
            """([selector, anchorAttr, anchorValue]) => {
                const message = document.querySelector(selector);
                if (!message) return false;
                return message.getAttribute(anchorAttr) !== anchorValue;
            }""",
            arg=[
                selectors.get("latest_outgoing_message")[0],
                ANCHOR_ATTR,
                before[0],
            ],
            timeout=15_000,
        )
        latest = page.locator(selectors.get("latest_outgoing_message")[0]).first
        await await_terminal_state(page, latest, "图片")
    except PageError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise PageError("图片消息已触发发送，但无法确认是否发送成功；为避免重复不会自动重试") from exc


async def send_sticker(page: Page, sticker: StickerRef) -> None:
    """发送抖音原生表情。"""

    before = await mark_latest_outgoing(page)

    button = await first_visible(page, ["sticker_button"], timeout_ms=5_000)
    if button is None:
        raise PageError("找不到表情按钮")
    await button.click(force=True)

    panel = await first_visible(page, ["sticker_panel"], timeout_ms=5_000)
    if panel is None:
        raise PageError("表情面板没有打开")

    # 先切到目标分类（面板默认只渲染第一屏）
    if sticker.category:
        try:
            category = panel.get_by_text(sticker.category, exact=True)
            if await category.count() and await category.first.is_visible():
                await category.first.click()
                await asyncio.sleep(0.4)
        except Exception as exc:  # noqa: BLE001
            logger.debug("切换表情分类失败: %s", exc)

    item = await _locate_sticker(panel, sticker)
    if item is None:
        raise PageError(f"在抖音表情面板中找不到原生表情: {sticker.name}")

    try:
        resource_key = await _sticker_resource_key(item)
    except Exception:  # noqa: BLE001
        resource_key = ""

    await item.click(force=True)

    try:
        await confirm_outgoing(page, before, f"原生表情“{sticker.name}”", resource_key=resource_key)
        return
    except PageError as exc:
        message = str(exc)

    # 情况一：页面提示可以重试 —— 点重试
    if "可以重试" in message:
        if await click_retry_on_latest(page):
            await confirm_outgoing(page, before, f"原生表情“{sticker.name}”", resource_key=resource_key)
            return

    # 情况二：表情只是插进了输入框，还没发出去 —— 补一次发送
    if await publish_ready(page):
        await trigger_send(page)
        await confirm_outgoing(page, before, f"原生表情“{sticker.name}”", resource_key=resource_key)
        return

    raise PageError(f"原生表情“{sticker.name}”发送未确认: {message}")


async def _locate_sticker(panel: Locator, sticker: StickerRef) -> Locator | None:
    """在表情面板里定位一个表情。

    依次尝试：
    1. 描述文本精确匹配（最准 —— 面板里显示的名字）
    2. role/aria-label/title/alt 精确匹配
    3. 分类内序号兜底
    """

    name = sticker.name

    # 1. 描述文本精确匹配
    for item_selector in selectors.get("sticker_item"):
        try:
            items = panel.locator(item_selector)
            count = await items.count()
        except Exception:  # noqa: BLE001
            continue
        for index in range(count):
            candidate = items.nth(index)
            try:
                for desc_selector in selectors.get("sticker_desc"):
                    desc = candidate.locator(desc_selector)
                    if await desc.count() == 0:
                        continue
                    text = (await desc.first.inner_text()).strip()
                    if text == name:
                        return candidate
            except Exception:  # noqa: BLE001
                continue

    # 2. 无障碍属性匹配
    try:
        for role in ("img", "button"):
            locator = panel.get_by_role(role, name=name, exact=True)
            if await locator.count() and await locator.first.is_visible():
                return locator.first
    except Exception:  # noqa: BLE001
        pass

    for attribute in ("aria-label", "title", "alt"):
        try:
            locator = panel.locator(f'[{attribute}="{name}"]')
            if await locator.count() and await locator.first.is_visible():
                return locator.first
        except Exception:  # noqa: BLE001
            continue

    # 3. 序号兜底
    if sticker.fallback_index is not None:
        try:
            items = panel.locator('[role="button"], img, [aria-label], [title]')
            if await items.count() > sticker.fallback_index:
                return items.nth(sticker.fallback_index)
        except Exception:  # noqa: BLE001
            pass

    return None


async def _sticker_resource_key(item: Locator) -> str:
    src = await item.get_attribute("src")
    if not src:
        image = item.locator("img").first
        if await image.count():
            src = await image.get_attribute("src")
    return _resource_key(src or "")
