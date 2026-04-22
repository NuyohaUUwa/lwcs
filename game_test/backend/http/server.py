"""Compatibility shell for the HTTP transport package."""

# pyright: reportMissingImports=false, reportMissingModuleSource=false, reportConstantRedefinition=false, reportMissingTypeArgument=false

import sys

_CURRENT_MODULE = sys.modules[__name__]
if __name__ == "backend.http.server":
    sys.modules.setdefault("game_test.backend.http.server", _CURRENT_MODULE)
elif __name__ == "game_test.backend.http.server":
    sys.modules.setdefault("backend.http.server", _CURRENT_MODULE)

from .app import app, create_app, run_development_server

__all__ = ["app", "create_app", "run_development_server"]


if __name__ == "__main__":
    run_development_server()
