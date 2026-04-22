"""Static asset and index entrypoints."""

import os

from flask import Blueprint, Flask, send_from_directory


GAME_TEST_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
WEB_DIR = os.path.join(GAME_TEST_DIR, "web")

blueprint = Blueprint("http_static", __name__)


@blueprint.route("/", methods=["GET"])
def index():
    return send_from_directory(WEB_DIR, "index.html")


@blueprint.route("/favicon.ico")
def favicon():
    return send_from_directory(WEB_DIR, "favicon.ico")


@blueprint.route("/<path:path>", methods=["GET"])
def static_files(path: str):
    return send_from_directory(WEB_DIR, path)


def register_static_routes(app: Flask) -> None:
    app.register_blueprint(blueprint)
