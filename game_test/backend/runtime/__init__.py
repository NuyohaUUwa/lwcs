"""运行时层：统一暴露长生命周期会话与共享运行时对象。"""

import sys

_CURRENT_MODULE = sys.modules[__name__]
if __name__ == "backend.runtime":
    sys.modules.setdefault("game_test.backend.runtime", _CURRENT_MODULE)
elif __name__ == "game_test.backend.runtime":
    sys.modules.setdefault("backend.runtime", _CURRENT_MODULE)

from .models import Item, RoleInfo
from .session import GameSession, get_session

__all__ = ["GameSession", "Item", "RoleInfo", "get_session"]
