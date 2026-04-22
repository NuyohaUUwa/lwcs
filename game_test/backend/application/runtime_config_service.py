"""Application-facing runtime server configuration facade."""

from dataclasses import dataclass

from game_test.backend.infrastructure.config import API_DEBUG, API_HOST, API_PORT


@dataclass(frozen=True)
class RuntimeServerConfig:
    host: str
    port: int
    debug: bool


def get_runtime_server_config() -> RuntimeServerConfig:
    return RuntimeServerConfig(host=API_HOST, port=API_PORT, debug=API_DEBUG)


__all__ = ["RuntimeServerConfig", "get_runtime_server_config"]
