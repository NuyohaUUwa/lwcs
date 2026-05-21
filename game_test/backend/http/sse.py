"""SSE endpoint registration and stream generation."""

import json
import queue

from flask import Blueprint, Flask, Response

from ..application.session_status_service import get_live_session


blueprint = Blueprint("http_sse", __name__, url_prefix="/api")


@blueprint.route("/events", methods=["GET"])
def api_events():
    session = get_live_session()
    q = session.subscribe_sse()

    def generate():
        try:
            yield f"data: {json.dumps({'type': 'status', 'data': session.get_status()}, ensure_ascii=False)}\n\n"
            yield (
                f"data: {json.dumps({'type': 'control_state', 'data': session.get_control_state()}, ensure_ascii=False)}"
                "\n\n"
            )
            yield (
                f"data: {json.dumps({'type': 'battle_state', 'data': session.get_status().get('battle_state', {})}, ensure_ascii=False)}"
                "\n\n"
            )
            yield f"data: {json.dumps({'type': 'gold', 'data': session.get_gold_snapshot()}, ensure_ascii=False)}\n\n"
            while True:
                try:
                    payload = q.get(timeout=20)
                    yield f"data: {payload}\n\n"
                except queue.Empty:
                    yield ": ping\n\n"
        except GeneratorExit:
            pass
        finally:
            session.unsubscribe_sse(q)

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def register_sse_routes(app: Flask) -> None:
    app.register_blueprint(blueprint)
