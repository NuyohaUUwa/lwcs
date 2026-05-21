"""配置值唯一实现入口。"""

import os
import sys

_CURRENT_MODULE = sys.modules[__name__]
if __name__ == "backend.infrastructure.config":
    sys.modules.setdefault("game_test.backend.infrastructure.config", _CURRENT_MODULE)
elif __name__ == "game_test.backend.infrastructure.config":
    sys.modules.setdefault("backend.infrastructure.config", _CURRENT_MODULE)

# 登录服务器配置
LOGIN_SERVERS = {
    "龙一服": {"ip": "8.141.22.68", "port": 9988},
    "龙二服": {"ip": "60.205.231.81", "port": 9991},
    "其他": {"ip": "8.141.22.68", "port": 9977},
}

# 游戏服务器配置（选角/进游戏）
GAME_SERVERS = {
    "龙一服": {"ip": "tlz.shuihl.cn", "port": 12065},
    "龙二服": {"ip": "tl10.shuihl.cn", "port": 12001},
    "生死符(推荐)": {"ip": "tl11.shuihl.cn", "port": 12001},
}

# Flask API 配置
# 使用 127.0.0.1 避免 Clash/TUN 驱动拦截 0.0.0.0 绑定导致的 WinError 10013
API_HOST = "127.0.0.1"


def _api_port_from_env() -> int:
    raw = (os.environ.get("LWCS_API_PORT") or "").strip()
    if not raw:
        return 7896
    try:
        return int(raw)
    except ValueError:
        return 7896


API_PORT = _api_port_from_env()
API_DEBUG = False

# 报文记录最大条数（环形缓冲）
PACKET_LOG_MAX = 500

# 发送队列每包间隔（秒）
SEND_INTERVAL = 1

# 接收缓冲区大小
RECV_BUFSIZE = 14048

# 循环战斗「启动延时」仅作后端冷启动 / 未传参时的回退；实际值应由前端请求体传入 loop_delay_ms
DEFAULT_BATTLE_LOOP_DELAY_MS = 1000
# 地图 NPC 默认兜底值（全项目统一使用）
DEFAULT_MAP_NPC_ID_HEX = "38900d00"
DEFAULT_MAP_NPC_UTF8_TEXT = "通用NPC"

__all__ = [
    "LOGIN_SERVERS",
    "GAME_SERVERS",
    "API_HOST",
    "API_PORT",
    "API_DEBUG",
    "PACKET_LOG_MAX",
    "SEND_INTERVAL",
    "RECV_BUFSIZE",
    "DEFAULT_BATTLE_LOOP_DELAY_MS",
    "DEFAULT_MAP_NPC_ID_HEX",
    "DEFAULT_MAP_NPC_UTF8_TEXT",
]
