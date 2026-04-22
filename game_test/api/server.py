"""兼容入口：保留 game_test.api.server，对外继续导出 app。"""

from game_test.backend.http.server import app, run_development_server

__all__ = ["app"]


if __name__ == "__main__":
    run_development_server()
