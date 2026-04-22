"""Flask app creation and transport assembly."""

# pyright: reportMissingImports=false, reportMissingModuleSource=false, reportConstantRedefinition=false, reportMissingTypeArgument=false

import sys

from flask import Flask

try:
    from flask_cors import CORS
except ModuleNotFoundError:  # 允许在未安装 flask-cors 的环境下降级启动
    CORS = None

_CURRENT_MODULE = sys.modules[__name__]
if __name__ == "backend.http.app":
    sys.modules.setdefault("game_test.backend.http.app", _CURRENT_MODULE)
elif __name__ == "game_test.backend.http.app":
    sys.modules.setdefault("backend.http.app", _CURRENT_MODULE)

from ..application.runtime_config_service import get_runtime_server_config
from ..application.session_status_service import bootstrap_control_worker
from .routes import register_api_routes
from .sse import register_sse_routes
from .static import register_static_routes


def create_app() -> Flask:
    app = Flask(__name__, static_folder=None)
    if CORS is not None:
        CORS(app)

    bootstrap_control_worker()
    register_api_routes(app)
    register_static_routes(app)
    register_sse_routes(app)
    return app


app = create_app()


def run_development_server() -> None:
    server_config = get_runtime_server_config()
    host = server_config.host
    port = server_config.port
    debug = server_config.debug
    print(f"游戏测试应用 API 服务启动中，地址: http://{host}:{port}")
    print(f"前端页面: http://127.0.0.1:{port}/")
    app.run(host=host, port=port, debug=debug, threaded=True)


__all__ = ["app", "create_app", "run_development_server"]
