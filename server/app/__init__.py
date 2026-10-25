"""个人工作台后端。

后端只做三件事：
1. 暴露 HTTP API（FastAPI）
2. 持有浏览器会话并执行抖音续火花任务
3. 把状态落进 SQLite
"""

__all__ = ["__version__"]

__version__ = "0.1.0"
