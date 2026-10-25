"""运行期配置。

所有可调项都能用环境变量覆盖，默认值适合在 Windows 上单人手跑。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

SERVER_DIR = Path(__file__).resolve().parent.parent

# README 承诺过 server/.env 可用。必须在下面任何 os.getenv 之前加载；
# 进程里已有的环境变量优先（load_dotenv 默认不覆盖）。
try:
    from dotenv import load_dotenv

    load_dotenv(SERVER_DIR / ".env")
except ImportError:  # 没装 python-dotenv 时退回纯环境变量
    pass

DATA_DIR = Path(os.getenv("DSW_DATA_DIR", SERVER_DIR / "data")).resolve()
DB_PATH = Path(os.getenv("DSW_DB_PATH", DATA_DIR / "workbench.db")).resolve()
PROFILES_DIR = Path(os.getenv("DSW_PROFILES_DIR", DATA_DIR / "profiles")).resolve()
ARTIFACTS_DIR = Path(os.getenv("DSW_ARTIFACTS_DIR", DATA_DIR / "artifacts")).resolve()
LOGS_DIR = Path(os.getenv("DSW_LOGS_DIR", DATA_DIR / "logs")).resolve()
WEB_DIST_DIR = Path(os.getenv("DSW_WEB_DIST", SERVER_DIR.parent / "web" / "dist")).resolve()


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    """一份只读配置快照。"""

    host: str = os.getenv("DSW_HOST", "127.0.0.1")
    port: int = _env_int("DSW_PORT", 8766)

    # 浏览器
    headless: bool = _env_bool("DSW_HEADLESS", True)
    browser_channel: str = os.getenv("DSW_BROWSER_CHANNEL", "chrome")
    browser_executable: str = os.getenv("DSW_BROWSER_EXECUTABLE", "")
    nav_timeout_ms: int = _env_int("DSW_NAV_TIMEOUT_MS", 45_000)

    # 抖音
    chat_url: str = os.getenv("DSW_CHAT_URL", "https://www.douyin.com/chat")

    # 调度
    planner_interval_seconds: int = _env_int("DSW_PLANNER_INTERVAL_SECONDS", 20)
    job_poll_seconds: int = _env_int("DSW_JOB_POLL_SECONDS", 3)
    send_interval_min_seconds: int = _env_int("DSW_SEND_INTERVAL_MIN_SECONDS", 3)
    send_interval_max_seconds: int = _env_int("DSW_SEND_INTERVAL_MAX_SECONDS", 8)

    # 限流保护
    daily_send_cap: int = _env_int("DSW_DAILY_SEND_CAP", 60)
    per_friend_daily_cap: int = _env_int("DSW_PER_FRIEND_DAILY_CAP", 1)

    # 留空表示不限制，形如 "23:00" / "08:00"
    quiet_hours_start: str = os.getenv("DSW_QUIET_HOURS_START", "")
    quiet_hours_end: str = os.getenv("DSW_QUIET_HOURS_END", "")

    retry_limit: int = _env_int("DSW_RETRY_LIMIT", 2)

    @property
    def quiet_hours_enabled(self) -> bool:
        return bool(self.quiet_hours_start and self.quiet_hours_end)

    def ensure_dirs(self) -> None:
        for directory in (DATA_DIR, PROFILES_DIR, ARTIFACTS_DIR, LOGS_DIR):
            directory.mkdir(parents=True, exist_ok=True)


settings = Settings()
settings.ensure_dirs()


def profile_dir_for(account_id: int) -> Path:
    """每个抖音账号一个独立的浏览器 profile 目录。

    独立 profile 是多账号场景下最重要的隔离：cookie、localStorage、缓存
    各不串，抖音也就看不到"同一个浏览器里有多个号"的信号。
    """

    return PROFILES_DIR / f"douyin-{account_id}"
