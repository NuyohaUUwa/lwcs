"""
游戏测试应用启动入口。

启动后访问：http://127.0.0.1:7896/
"""

from game_test.backend.infrastructure.config import API_DEBUG, API_HOST, API_PORT
from game_test.backend.infrastructure.packet_log_service import init_packet_log_session
from game_test.backend.infrastructure.paths import ensure_data_directories

ensure_data_directories()

from game_test.api.server import app

if __name__ == "__main__":
    init_packet_log_session()
    print("=" * 50)
    print("  游戏测试应用")
    print("=" * 50)
    print(f"  API: http://{API_HOST}:{API_PORT}/api/")
    print(f"  前端: http://{API_HOST}:{API_PORT}/")
    print("=" * 50)
    app.run(host=API_HOST, port=API_PORT, debug=API_DEBUG, threaded=True)
