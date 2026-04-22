"""
game_test 后端新包骨架。

依赖方向在本阶段冻结为：
http -> application -> domain/runtime/infrastructure
"""

import sys

from .architecture import ALLOWED_DEPENDENCIES, LAYER_ORDER, describe_dependency_direction

_CURRENT_MODULE = sys.modules[__name__]
if __name__ == "backend":
    sys.modules.setdefault("game_test.backend", _CURRENT_MODULE)
elif __name__ == "game_test.backend":
    sys.modules.setdefault("backend", _CURRENT_MODULE)

__all__ = ["ALLOWED_DEPENDENCIES", "LAYER_ORDER", "describe_dependency_direction"]
