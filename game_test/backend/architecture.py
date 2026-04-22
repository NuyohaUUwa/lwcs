"""
后端分层骨架声明。

该模块只提供结构约束，不承载业务逻辑，供后续任务统一参考，
避免在迁移过程中打破约定的依赖方向。
"""

LAYER_ORDER = ("http", "application", "domain", "runtime", "infrastructure")
TERMINAL_LAYERS = ("domain", "runtime", "infrastructure")

ALLOWED_DEPENDENCIES = {
    "http": ("application",),
    "application": TERMINAL_LAYERS,
    "domain": (),
    "runtime": (),
    "infrastructure": (),
}


def describe_dependency_direction() -> str:
    return "http -> application -> domain/runtime/infrastructure"
