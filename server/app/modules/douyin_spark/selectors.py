"""抖音 Web 私信页的选择器。

**为什么要单独一个文件**：抖音前端改版是常态而非意外。选择器散落在业务代码里，
一旦改版就要满仓库找。这里集中一处，并支持用 ``selectors.yaml`` 覆盖，
改版时只改配置、不动代码。

每个选择器都是一个"候选列表"，按顺序取第一个可见的 —— 抖音经常同时渲染
多个版本的节点（改版灰度），全列出来比只留最新的更耐操。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger("douyin-spark")

SELECTORS_FILE = Path(__file__).with_name("selectors.yaml")

# ── 默认值 ────────────────────────────────────────────────────────────────
# 下面每一条都来自对抖音 Web 私信页的实际观察，注释里标了用途。

DEFAULTS: dict[str, Any] = {
    # 登录态判定：这些文案出现 = 需要登录
    "login_required": [
        "text=扫码登录",
        "text=验证码登录",
        "text=登录后",
    ],
    # 风控/安全验证：出现即需人工介入
    "risk_markers": [
        "text=安全验证",
        "text=完成验证",
        "text=验证身份",
        "text=操作频繁",
    ],
    # 已登录的判据：cookie 里有这些名字
    "logged_in_cookies": ["sessionid", "sessionid_ss"],

    # 顶部搜索框（用来找好友）
    "search_input": [
        ".LeftPanelHeadersearch input",
        'input[placeholder*="搜索"]',
        '[role="textbox"][placeholder*="搜索"]',
    ],
    # 搜索结果里的"聊天"按钮
    "chat_button": [
        'div[class*="SearchPanelitemchat_btn"]',
        '[class*="chat_btn"]',
        '[class*="SearchPanel"] [class*="btn"]',
    ],
    # 搜索结果条目
    "search_item": [
        '[class*="SearchPanelitembox"]',
        '[class*="SearchPanelitem-box"]',
        '[class*="SearchPanelitem_box"]',
    ],
    # 搜索结果条目里的昵称
    "search_item_name": [
        '[class*="SearchPanelitemtitle"]',
        '[class*="SearchPanelitemTitle"]',
        '[class*="SearchPanelitem_title"]',
        '[class*="SearchPanelitem-title"]',
        '[class*="SearchPanelitemname"]',
        '[class*="SearchPanelitemName"]',
        '[class*="SearchPanelitem_name"]',
        '[class*="SearchPanelitem-name"]',
    ],

    # 左侧会话列表
    "conversation_item": [
        '[data-e2e="conversation-item"]',
        ".conversationConversationItemwrapper",
        '[class*="conversationConversationItem"]',
    ],
    "conversation_title": [
        ".conversationConversationItemtitle",
        '[class*="ConversationItemtitle"]:not([class*="Wrapper"]):not([class*="wrapper"])',
    ],
    "conversation_list": [
        ".conversationConversationListwrapper",
        '[class*="ConversationListwrapper"]',
        '[class*="conversationList-wrapper"]',
    ],
    "no_more": [
        '[class*="no-more-tip-"]',
        '[class*="noMoreTip"]',
    ],

    # 当前会话面板顶部的标题（好友名，可能混着火花文案）
    "conversation_panel_title": [
        '.RightPanelHeadertitleContainer',
        '[class*="RightPanelHeader"]',
        '[class*="RightPanel"] [class*="Header"]',
    ],
    # 火花天数徽标：会话条目内 和 右侧会话面板顶部 各有一份。
    # 状态（灰=今天没续）编码在火焰图标的 URL 里（如 flame_icon/normal/gray_normal.png）
    "streak_icon": [
        "img.commonStreakicon",
        '[class*="streakContainer"] img',
    ],
    "streak_badge": [
        ".commonStreaknormalText",
        '[class*="commonStreak"]',
    ],
    "spark_icon": [
        '.RightPanelHeadertitleContainer img.commonStreakicon',
        '[class*="RightPanelHeader"] img.commonStreakicon',
        '[class*="RightPanel"] [class*="Header"] img.commonStreakicon',
        '.RightPanelHeadertitleContainer [class*="streakContainer"] img',
    ],
    "spark_status": [
        '.RightPanelHeadertitleContainer .commonStreaknormalText',
        '[class*="RightPanelHeader"] .commonStreaknormalText',
        '[class*="RightPanel"] [class*="Header"] .commonStreaknormalText',
        # 正常态的类名带 normal，灰色/临期变体类名不同 ——
        # 下面这几条兜底让灰色态也能被读到（状态由类名归类）
        '.RightPanelHeadertitleContainer [class*="commonStreak"]',
        '[class*="RightPanelHeader"] [class*="commonStreak"]',
        '[class*="RightPanel"] [class*="Header"] [class*="commonStreak"]',
    ],

    # 消息输入框（contenteditable，不是 textarea）
    "message_input": [
        'div[data-slate-editor="true"][contenteditable="true"]',
        'div[contenteditable="true"][data-slate-editor]',
        '[contenteditable="true"][data-placeholder*="发送消息"]',
        'div[contenteditable="true"]',
    ],
    # 发送按钮
    "send_button": [
        '[class*="messageMsgInputpublishBtn"]',
        ".e2e-send-msg-bt",
        'button[aria-label*="发送"]',
        '[role="button"][aria-label*="发送"]',
    ],
    # 图片上传控件
    "image_input": [
        'input[type="file"][accept*="image"]',
        'input[type="file"]',
    ],
    # 表情面板入口
    "sticker_button": [
        "svg.messageMsgInputiconAction",
        'button[aria-label*="表情"]',
        '[role="button"][aria-label*="表情"]',
        '[title*="表情"]',
    ],
    "sticker_panel": [
        ".componentsemojiemojiPanel",
        '[class*="emojiPanel"]',
        '[role="dialog"]',
        '[class*="sticker"]',
    ],
    "sticker_item": [
        ".emojiEmojiItememojiItem",
        '[class*="emojiItem"]',
    ],
    "sticker_desc": [
        ".emojiEmojiItememojiItemDesc",
        '[class*="emojiItemDesc"]',
    ],
    # 表情面板里的分类 tab
    "sticker_category": [
        '[class*="emojiCategor"] [class*="item"]',
        '[class*="emojiTab"]',
        '[class*="category"] [class*="tab"]',
    ],

    # 「最后一条自己发出的消息」：用于确认发送是否真的成功
    "latest_outgoing_message": [
        '.messageMessageListlist [data-index="0"] '
        ".messageMessageBoxmessageBox:has("
        ".messageMessageBoxcontentBox.messageMessageBoxisFromMe)",
    ],
    # 发送失败标记（红色感叹号 / 重试）
    "send_failure_markers": [
        "text=发送失败",
        '[aria-label*="重试"]',
        '[title*="重试"]',
        '[class*="sendFailed"]',
        '[class*="SendFailed"]',
        '[class*="ContentSideSendStatusretry"]',
        '[class*="SendStatusretry"]',
    ],
    # 发送中的转圈
    "send_pending_markers": [
        ".semi-spin",
        '[class*="im-saas-message-spin"]',
        '[data-icon="spin"]',
    ],
    "send_retry_markers": [
        '[aria-label*="重试"]',
        '[title*="重试"]',
        '[class*="ContentSideSendStatusretry"]',
        '[class*="SendStatusretry"]',
    ],
}


def _load_overrides() -> dict[str, Any]:
    """读 ``selectors.yaml`` 覆盖默认值。文件不存在就用默认值。"""

    if not SELECTORS_FILE.is_file():
        return {}
    try:
        import yaml  # type: ignore[import-untyped]

        loaded = yaml.safe_load(SELECTORS_FILE.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - 配置文件坏了不该拖垮整个模块
        logger.warning("selectors.yaml 解析失败，忽略覆盖: %s", exc)
        return {}
    if not isinstance(loaded, dict):
        logger.warning("selectors.yaml 顶层不是字典，忽略覆盖")
        return {}
    return {key: value for key, value in loaded.items() if isinstance(value, list)}


_OVERRIDES = _load_overrides()

SELECTORS: dict[str, list[str]] = {
    key: [str(item) for item in value]
    for key, value in {**DEFAULTS, **_OVERRIDES}.items()
}


def get(name: str) -> list[str]:
    """取某个选择器的候选列表。"""

    return SELECTORS.get(name, [])


def as_dict() -> dict[str, list[str]]:
    """给前端看的"当前生效的选择器"，便于排查改版问题。"""

    return {key: list(value) for key, value in SELECTORS.items()}
