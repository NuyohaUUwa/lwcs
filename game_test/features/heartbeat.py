"""
心跳策略。
保留超时检测与发送节奏；实际发包走 action_manager。
"""

import threading
import time

from utils.random_num import random_num_hex4

HEARTBEAT_SLEEP_S = 10
STALE_WARN_S = 60
STALE_TIMEOUT_S = 90
HEARTBEAT_PACKET_TEMPLATE = "12000000e8030a000a04{random_num}f5050704000000000000"


def build_heartbeat_packet() -> str:
    return HEARTBEAT_PACKET_TEMPLATE.format(random_num=random_num_hex4())


def start_heartbeat(stop_event: threading.Event, on_timeout) -> threading.Thread:
    """启动心跳线程。"""

    def _thread():
        from core.session import get_session
        from services.action_manager import send_raw_action

        session = get_session()
        session.last_recv_ts = time.time()
        print("[heartbeat] 心跳线程已启动")
        stale_warned = False

        while not stop_event.is_set():
            try:
                if session.connected and session.sock and not stop_event.is_set():
                    elapsed = time.time() - session.last_recv_ts
                    if elapsed > STALE_TIMEOUT_S:
                        print(f"[heartbeat] {elapsed:.0f}s 未收到服务器数据，触发掉线处理")
                        try:
                            on_timeout()
                        except Exception as cb_err:
                            print(f"[heartbeat] on_timeout 调用异常: {cb_err}")
                        break

                    if elapsed > STALE_WARN_S and not stale_warned:
                        session.notify_status_change()
                        stale_warned = True
                    elif elapsed <= STALE_WARN_S:
                        stale_warned = False

                    res = send_raw_action(build_heartbeat_packet(), priority=1, use_queue=True)
                    if not res.get("ok"):
                        print(f"[heartbeat] 发送心跳包失败: {res.get('error')}")

                time.sleep(HEARTBEAT_SLEEP_S)
            except Exception as e:
                print(f"[heartbeat] 心跳线程异常: {e}")
                break

        print("[heartbeat] 心跳线程退出")

    t = threading.Thread(target=_thread, daemon=True, name="heartbeat")
    t.start()
    return t
