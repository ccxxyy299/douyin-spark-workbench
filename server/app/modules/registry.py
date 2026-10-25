"""工作台模块注册表。

工作台内核只负责：模块注册、导航、通用布局。
每个业务模块自成一个包，声明自己的路由前缀、标题、图标和所需能力。

这样后续要加"定时发布""数据看板"之类的模块时，是加模块而不是改内核。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ModuleSpec:
    """一个模块的元信息。"""

    id: str
    name: str
    description: str
    icon: str
    # 挂载到 app 的路由前缀
    prefix: str
    # 前端页面用的路径前缀。必须和 prefix 分开 ——
    # 浏览器地址走 /api/... 会被后端当成接口请求（刷新即 422）
    route_prefix: str
    # 该模块提供的导航项
    nav: list[dict[str, Any]] = field(default_factory=list)


_MODULES: dict[str, ModuleSpec] = {}


def register(spec: ModuleSpec) -> ModuleSpec:
    _MODULES[spec.id] = spec
    return spec


def all_modules() -> list[ModuleSpec]:
    return list(_MODULES.values())


def get_module(module_id: str) -> ModuleSpec | None:
    return _MODULES.get(module_id)
