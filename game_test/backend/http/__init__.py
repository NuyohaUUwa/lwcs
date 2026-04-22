"""HTTP 传输层入口包，仅负责 Flask / HTTP / SSE / 静态资源适配。"""

import sys

_CURRENT_PACKAGE = sys.modules[__name__]
if __name__ == "backend.http":
    sys.modules.setdefault("game_test.backend.http", _CURRENT_PACKAGE)
elif __name__ == "game_test.backend.http":
    sys.modules.setdefault("backend.http", _CURRENT_PACKAGE)
