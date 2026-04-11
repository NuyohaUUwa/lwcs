"""
游戏测试应用启动入口。

启动后访问：http://127.0.0.1:7896/
"""

import sys
import os

# 将 game_test 目录加入模块搜索路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from api.server import app
from config import API_HOST, API_PORT, API_DEBUG
from services.packet_log_service import init_packet_log_session

if __name__ == "__main__":
    init_packet_log_session()
    print("=" * 50)
    print("  游戏测试应用")
    print("=" * 50)
    print(f"  API: http://127.0.0.1:{API_PORT}/api/")
    print(f"  前端: http://127.0.0.1:{API_PORT}/")
    print("=" * 50)
    app.run(host=API_HOST, port=API_PORT, debug=API_DEBUG, threaded=True)
