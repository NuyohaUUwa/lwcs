"""
启动期原始报文日志持久化。

- 每次后端启动初始化一个新的会话日志文件
- 收发报文按 JSON Lines 追加写入
- 最多保留最新 10 个会话文件，旧文件自动删除
"""

import json
import os
import threading
import time

from paths import PACKET_LOG_DIR
MAX_PACKET_LOG_FILES = 10

_log_lock = threading.Lock()
_current_log_path: str | None = None


def _build_session_log_path() -> str:
    ts = time.strftime("%Y%m%d-%H%M%S", time.localtime())
    ms = int((time.time() % 1) * 1000)
    pid = os.getpid()
    return os.path.join(PACKET_LOG_DIR, f"packet-session-{ts}-{ms:03d}-{pid}.jsonl")


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
    global _current_log_path

    with _log_lock:
        if _current_log_path:
            return _current_log_path

        try:
            os.makedirs(PACKET_LOG_DIR, exist_ok=True)
            _current_log_path = _build_session_log_path()
            with open(_current_log_path, "a", encoding="utf-8"):
                pass
            _prune_old_logs()
        except Exception as e:
            print(f"[packet_log] 初始化日志会话失败: {e}")
            _current_log_path = ""
        return _current_log_path


def append_packet_record(record: dict) -> None:
    path = init_packet_log_session()
    if not path:
        return

    try:
        line = json.dumps(record, ensure_ascii=False)
        with _log_lock:
            with open(path, "a", encoding="utf-8") as f:
                f.write(line)
                f.write("\n")
    except Exception as e:
        print(f"[packet_log] 追加报文日志失败: {e}")
