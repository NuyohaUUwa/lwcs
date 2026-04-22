"""基础设施层：放置网络、持久化、配置与外部资源适配器。"""

import sys

_CURRENT_MODULE = sys.modules[__name__]
if __name__ == "backend.infrastructure":
    sys.modules.setdefault("game_test.backend.infrastructure", _CURRENT_MODULE)
elif __name__ == "game_test.backend.infrastructure":
    sys.modules.setdefault("backend.infrastructure", _CURRENT_MODULE)

__all__ = ["config", "paths", "data_manager", "packet_log_service"]
