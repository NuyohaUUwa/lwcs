"""HTTP API route registration grouped by transport capability."""

from flask import Flask

from .auth import blueprint as auth_blueprint
from .battle import blueprint as battle_blueprint
from .flow import blueprint as flow_blueprint
from .inventory import blueprint as inventory_blueprint
from .packet import blueprint as packet_blueprint
from .settings import blueprint as settings_blueprint
from .status import blueprint as status_blueprint
from .world import blueprint as world_blueprint


def register_api_routes(app: Flask) -> None:
    app.register_blueprint(status_blueprint)
    app.register_blueprint(auth_blueprint)
    app.register_blueprint(inventory_blueprint)
    app.register_blueprint(battle_blueprint)
    app.register_blueprint(world_blueprint)
    app.register_blueprint(packet_blueprint)
    app.register_blueprint(settings_blueprint)
    app.register_blueprint(flow_blueprint)
