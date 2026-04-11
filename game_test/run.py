"""
游戏测试应用启动入口。

启动后访问：http://127.0.0.1:7896/
"""

import sys
import os

# 将 game_test 目录加入模块搜索路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from paths import ensure_data_directories

ensure_data_directories()

from api.server import app
from config import API_DEBUG, API_HOST, API_PORT
from services.packet_log_service import init_packet_log_session

if __name__ == "__main__":
    init_packet_log_session()
    print("=" * 50)
    print("  游戏测试应用")
    print("=" * 50)
    print(f"  API: http://{API_HOST}:{API_PORT}/api/")
    print(f"  前端: http://{API_HOST}:{API_PORT}/")
    print("=" * 50)
    app.run(host=API_HOST, port=API_PORT, debug=API_DEBUG, threaded=True)
