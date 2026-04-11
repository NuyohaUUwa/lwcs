# 游戏服务器配置（与 main-000.py 保持同步）

# 登录服务器配置
LOGIN_SERVERS = {
    "龙一服": {"ip": "8.141.22.68", "port": 9988},
    "龙二服": {"ip": "60.205.231.81", "port": 9991},
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
API_PORT = 7896
API_DEBUG = False

# 报文记录最大条数（环形缓冲）
PACKET_LOG_MAX = 500

# 发送队列每包间隔（秒）
SEND_INTERVAL = 1

# 接收缓冲区大小
RECV_BUFSIZE = 14048

# 标注持久化文件路径（相对于 game_test 目录）
ANNOTATIONS_FILE = "data/annotations.json"
