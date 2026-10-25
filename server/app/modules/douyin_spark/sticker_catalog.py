"""抖音表情面板的采集器。

用途：把抖音表情面板里的表情抓成本地目录，用户在 UI 上点一下就能存成模板，
不用去猜"这个表情在面板里叫什么"。

抖音表情面板的 DOM 结构没有稳定的语义类名，分类 tab 尤其不友好。所以这里用
**渐进式发现**：先试几种结构特征去定位分类 tab，定位不到就退化成"只采集当前
可见的表情"。采集结果一律带缩略图，用户可以在 UI 上肉眼确认 —— 猜名字的活
交给用户看图，不要交给代码猜。
"""

from __future__ import annotations

import base64
import logging
from typing import Any

from playwright.async_api import Page

from app.modules.douyin_spark import selectors
from app.modules.douyin_spark.chat_page import first_visible

logger = logging.getLogger("douyin-spark")

# 在面板里找分类 tab 的几种结构特征，按可能性排序
_CATEGORY_HINTS = (
    "[class*='emojiPanel'] [class*='categor'] [class*='item']",
    "[class*='emojiPanel'] [class*='Categor'] [class*='Item']",
    "[class*='emojiPanel'] [class*='tab']",
    "[class*='emojiPanel'] [class*='Tab']",
    "[class*='emojiPanel'] [class*='side'] [class*='item']",
    "[class*='emojiPanel'] [class*='left'] [class*='item']",
)

# 采集面板当前可见项的 JS
_EXTRACT_JS = """
(category) => {
    const panel = document.querySelector('.componentsemojiemojiPanel')
              || document.querySelector('[class*="emojiPanel"]');
    if (!panel) return [];
    const out = [];
    const seen = new Set();
    const candidates = panel.querySelectorAll(
        '.emojiEmojiItememojiItem, [class*="emojiItem"]'
    );
    candidates.forEach((item, index) => {
        const desc = item.querySelector('.emojiEmojiItememojiItemDesc, [class*="emojiItemDesc"]');
        const name = desc ? (desc.textContent || '').trim() : '';
        const img = item.querySelector('img');
        const src = img ? (img.getAttribute('src') || '') : '';
        const key = category + '|' + (name || src || String(index));
        if (seen.has(key)) return;
        seen.add(key);
        out.push({
            name: name,
            index: index,
            src: src,
            resource_key: src ? src.split('/').pop().split('?')[0] : '',
        });
    });
    return out;
}
"""


async def open_sticker_panel(page: Page) -> None:
    """打开表情面板。"""

    button = await first_visible(page, ["sticker_button"], timeout_ms=5_000)
    if button is None:
        raise RuntimeError("找不到表情按钮")
    await button.click(force=True)
    panel = await first_visible(page, ["sticker_panel"], timeout_ms=5_000)
    if panel is None:
        raise RuntimeError("表情面板没有打开")


async def discover_categories(page: Page) -> list[str]:
    """尽力找出表情面板的分类名列表。

    抖音改过几次面板结构，这里对多种结构特征都做尝试；找不到就返回空列表，
    由调用方退化成"只采集可见项"。
    """

    for hint in _CATEGORY_HINTS:
        try:
            names = await page.evaluate(
                """(hint) => {
                    const panel = document.querySelector('.componentsemojiemojiPanel')
                              || document.querySelector('[class*="emojiPanel"]');
                    if (!panel) return [];
                    const nodes = panel.querySelectorAll(hint);
                    const out = [];
                    for (const node of nodes) {
                        const name = (node.getAttribute('aria-label')
                            || node.getAttribute('title')
                            || (node.textContent || '')).trim();
                        if (name && name.length <= 12) out.push(name);
                    }
                    return out;
                }""",
                hint,
            )
            if names:
                logger.info("表情分类发现成功（命中 %s）：%s", hint, names)
                return list(dict.fromkeys(names))
        except Exception:  # noqa: BLE001
            continue
    logger.info("未能发现表情分类，退化为只采集当前可见的表情")
    return []


async def collect_sticker_catalog(page: Page, *, include_preview: bool = True) -> list[dict[str, Any]]:
    """采集表情面板。

    返回 ``[{name, category, fallback_index, resource_key, preview}]``。
    ``preview`` 是 data URI 缩略图（供前端展示，不落盘）。
    """

    await open_sticker_panel(page)

    categories = await discover_categories(page)
    collected: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    async def harvest(category: str) -> None:
        for item in await page.evaluate(_EXTRACT_JS, category):
            name = (item.get("name") or "").strip()
            if not name:
                continue
            key = (category, name)
            if key in seen:
                continue
            seen.add(key)
            entry: dict[str, Any] = {
                "name": name,
                "category": category,
                "fallback_index": int(item.get("index") or 0),
                "resource_key": item.get("resource_key") or "",
                "preview": "",
            }
            if include_preview:
                entry["preview"] = await _capture_preview(page, item.get("src") or "")
            collected.append(entry)

    if categories:
        for category in categories:
            try:
                clicked = await page.evaluate(
                    """(name) => {
                        const panel = document.querySelector('.componentsemojiemojiPanel')
                                  || document.querySelector('[class*="emojiPanel"]');
                        if (!panel) return false;
                        const nodes = panel.querySelectorAll('*');
                        for (const node of nodes) {
                            const label = (node.getAttribute('aria-label')
                                || node.getAttribute('title')
                                || (node.textContent || '')).trim();
                            if (label === name && node.children.length === 0) {
                                node.click();
                                return true;
                            }
                        }
                        return false;
                    }""",
                    category,
                )
                if not clicked:
                    continue
                await page.wait_for_timeout(500)
            except Exception as exc:  # noqa: BLE001
                logger.debug("切换表情分类 %s 失败: %s", category, exc)
                continue
            await harvest(category)
    else:
        await harvest("")

    return collected


async def _capture_preview(page: Page, src: str) -> str:
    """把表情缩略图转成 data URI 存进库里。"""

    if not src:
        return ""
    if src.startswith("data:"):
        return src
    try:
        response = await page.request.get(src, timeout=10_000)
        if not response.ok:
            return ""
        body = await response.body()
        if not body or len(body) > 256 * 1024:
            return ""
        content_type = response.headers.get("content-type", "image/png")
        encoded = base64.b64encode(body).decode("ascii")
        return f"data:{content_type};base64,{encoded}"
    except Exception as exc:  # noqa: BLE001
        logger.debug("抓取表情缩略图失败: %s", exc)
        return ""
