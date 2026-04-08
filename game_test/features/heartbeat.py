"""
心跳管理模块（对齐 main-000.py _heartbeat_thread 实现）：
- 每 HEARTBEAT_SLEEP_S 秒向游戏服发送客户端心跳报文，维持连接
- 同时检测 STALE_TIMEOUT_S 秒内无 DN 包则判定掉线并回调 on_timeout
"""

import threading
import time

# ---- 参数 ----
HEARTBEAT_SLEEP_S = 5      # 每 5 秒循环一次（与 main-000.py time.sleep(5) 保持一致）
STALE_WARN_S      = 60     # 60 秒无响应：通过 SSE 立即推送心跳超时警告到前端
STALE_TIMEOUT_S   = 90     # 90 秒未收到任何服务器数据则判定掉线

# 客户端心跳报文（来自 main-000.py _heartbeat_thread）
HEARTBEAT_PACKET = "12000000e80302000504000015250204000000000000"


def start_heartbeat(stop_event: threading.Event, on_timeout) -> threading.Thread:
    """
    启动心跳线程（与 recv / send worker 共用同一 stop_event）。

    结构对齐 main-000.py _heartbeat_thread：
        while running:
            if socket connected:
                (check stale + send heartbeat)
            sleep(5)

    Args:
        stop_event:  连接断开时的停止信号。
        on_timeout:  超时回调，通常为 _on_disconnect(Exception)。

    Returns:
        已启动的 daemon 心跳线程。
    """
    def _thread():
        from core.session import get_session
        from core.connector import enqueue_packet
        from features.packet_probe import record_packet

        session = get_session()
        # 初始化收包时间戳，避免进入游戏后立即触发超时
        session.last_recv_ts = time.time()
        print("[heartbeat] 心跳线程已启动")

        stale_warned = False   # 60s 警告是否已推送

        while not stop_event.is_set():
            try:
                if session.connected and session.sock and not stop_event.is_set():
                    elapsed = time.time() - session.last_recv_ts

                    # ---- 90s 超时：触发断连 ----
                    if elapsed > STALE_TIMEOUT_S:
                        print(f"[heartbeat] {elapsed:.0f}s 未收到服务器数据，触发掉线处理")
                        try:
                            on_timeout()
                        except Exception as cb_err:
                            print(f"[heartbeat] on_timeout 调用异常: {cb_err}")
                        break

                    # ---- 60s 预警：立即通过 SSE 推送警告到前端 ----
                    if elapsed > STALE_WARN_S and not stale_warned:
                        print(f"[heartbeat] {elapsed:.0f}s 无响应，通过 SSE 推送心跳超时警告")
                        session.notify_status_change()
                        stale_warned = True
                    elif elapsed <= STALE_WARN_S:
                        stale_warned = False  # 连接恢复后重置

                    # ---- 发送客户端心跳包 ----
                    try:
                        record_packet(HEARTBEAT_PACKET, "UP")
                        enqueue_packet(session.send_queue, HEARTBEAT_PACKET, priority=1)
                        print(f"[heartbeat] 已发送心跳包，距上次收包 {elapsed:.0f}s")
                    except Exception as send_err:
                        print(f"[heartbeat] 发送心跳包失败: {send_err}")

                time.sleep(HEARTBEAT_SLEEP_S)

            except Exception as e:
                print(f"[heartbeat] 心跳线程异常: {e}")
                break

        print("[heartbeat] 心跳线程退出")

    t = threading.Thread(target=_thread, daemon=True, name="heartbeat")
    t.start()
    return t
