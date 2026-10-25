"""抖音续火花模块。

分层：

* ``selectors``     —— 所有 DOM 选择器（改版时只改这一处 / selectors.yaml）
* ``browser``       —— Playwright 上下文管理（每账号一个 profile）
* ``chat_page``     —— 私信页的页面对象（找好友、读火花）
* ``senders``       —— 文字 / 图片 / 原生表情的发送
* ``verifier``      —— 发送终态确认（最容易写错的一环）
* ``sticker_catalog``—— 表情面板采集
* ``executor``      —— 任务执行
* ``service``       —— 业务逻辑
* ``planner``       —— 调度
* ``router``        —— HTTP 接口
"""
