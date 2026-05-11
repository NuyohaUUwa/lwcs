"""
启动期原始报文日志持久化唯一实现入口。

- 每次后端启动初始化一个新的会话日志文件
- 收发报文按 JSON Lines 追加写入
- 最多保留最新 10 个会话文件，旧文件自动删除
"""

import json
import os
import sys
import threading
import time

from .paths import PACKET_LOG_DIR

_CURRENT_MODULE = sys.modules[__name__]
if __name__ == "backend.infrastructure.packet_log_service":
    sys.modules.setdefault("game_test.backend.infrastructure.packet_log_service", _CURRENT_MODULE)
elif __name__ == "game_test.backend.infrastructure.packet_log_service":
    sys.modules.setdefault("backend.infrastructure.packet_log_service", _CURRENT_MODULE)

MAX_PACKET_LOG_FILES = 10
MAX_PACKET_LOG_LINES = 100000
MAX_LOG_RAW_HEX_CHARS = 2048
MAX_LOG_UTF8_TEXT_CHARS = 512

_log_lock = threading.Lock()
_current_log_path: str | None = None
_current_log_lines = 0


def _build_session_log_path() -> str:
    ts = time.strftime("%Y%m%d-%H%M%S", time.localtime())
    ms = int((time.time() % 1) * 1000)
    pid = os.getpid()
    return os.path.join(PACKET_LOG_DIR, f"packet-session-{ts}-{ms:03d}-{pid}.jsonl")


def _count_file_lines(path: str) -> int:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return sum(1 for _ in f)
    except Exception:
        return 0


def _list_session_logs() -> list[str]:
    if not os.path.isdir(PACKET_LOG_DIR):
        return []
    files = []
    for name in os.listdir(PACKET_LOG_DIR):
        if name.endswith(".jsonl"):
            files.append(os.path.join(PACKET_LOG_DIR, name))
    files.sort(key=os.path.getmtime, reverse=True)
    return files


def _prune_old_logs() -> None:
    for path in _list_session_logs()[MAX_PACKET_LOG_FILES:]:
        try:
            os.remove(path)
        except Exception as e:
            print(f"[packet_log] 删除旧日志失败 {os.path.basename(path)}: {e}")


def init_packet_log_session() -> str:
    global _current_log_path, _current_log_lines

    with _log_lock:
        if _current_log_path:
            return _current_log_path

        try:
            os.makedirs(PACKET_LOG_DIR, exist_ok=True)
            _current_log_path = _build_session_log_path()
            with open(_current_log_path, "a", encoding="utf-8"):
                pass
            _current_log_lines = _count_file_lines(_current_log_path)
            _prune_old_logs()
        except Exception as e:
            print(f"[packet_log] 初始化日志会话失败: {e}")
            _current_log_path = ""
            _current_log_lines = 0
        return _current_log_path


def _rotate_log_if_needed() -> None:
    global _current_log_path, _current_log_lines
    if _current_log_lines < MAX_PACKET_LOG_LINES:
        return
    _current_log_path = _build_session_log_path()
    with open(_current_log_path, "a", encoding="utf-8"):
        pass
    _current_log_lines = 0
    _prune_old_logs()


def _truncate_text(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[:limit] + "…"


def _build_compact_record(record: dict) -> dict:
    compact = dict(record)

    # 冗余字段可由 ts 还原，落盘时删除
    compact.pop("ts_str", None)

    # 空字段不落盘
    if not compact.get("annotation"):
        compact.pop("annotation", None)
    if compact.get("map_npc") is None:
        compact.pop("map_npc", None)
    if compact.get("map_npc_list") is None:
        compact.pop("map_npc_list", None)

    # parsed 仅保留必要信息，并限制 utf8 文本长度
    parsed = compact.get("parsed")
    if isinstance(parsed, dict):
        parsed_copy = dict(parsed)
        utf8_text = parsed_copy.get("utf8_text")
        if isinstance(utf8_text, str):
            parsed_copy["utf8_text"] = _truncate_text(utf8_text, MAX_LOG_UTF8_TEXT_CHARS)
        compact["parsed"] = parsed_copy
    elif parsed is None:
        compact.pop("parsed", None)

    # 原始报文过长时仅保留首尾，显著减小落盘体积
    raw_hex = compact.get("raw_hex")
    if isinstance(raw_hex, str) and len(raw_hex) > MAX_LOG_RAW_HEX_CHARS:
        separator = "...[truncated]..."
        available = MAX_LOG_RAW_HEX_CHARS - len(separator)
        keep_head = available // 2
        keep_tail = available - keep_head
        compact["raw_hex_len"] = len(raw_hex)
        compact["raw_hex"] = f"{raw_hex[:keep_head]}{separator}{raw_hex[-keep_tail:]}"
        compact["raw_hex_truncated"] = True

    return compact


def append_packet_record(record: dict) -> None:
    global _current_log_lines
    path = init_packet_log_session()
    if not path:
        return

    try:
        line = json.dumps(_build_compact_record(record), ensure_ascii=False, separators=(",", ":"))
        with _log_lock:
            _rotate_log_if_needed()
            path = _current_log_path or path
            with open(path, "a", encoding="utf-8") as f:
                f.write(line)
                f.write("\n")
            _current_log_lines += 1
    except Exception as e:
        print(f"[packet_log] 追加报文日志失败: {e}")


__all__ = ["MAX_PACKET_LOG_FILES", "MAX_PACKET_LOG_LINES", "init_packet_log_session", "append_packet_record"]
