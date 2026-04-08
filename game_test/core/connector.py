"""
TCP 连接管理：建立连接、收包循环线程、带优先队列的发包工作线程。
"""

import socket
import threading
import binascii
import time
import queue
from typing import Callable, Optional

from config import RECV_BUFSIZE, SEND_INTERVAL


def connect(ip: str, port: int, timeout: float = 15.0) -> socket.socket:
    """
    建立 TCP 连接，返回 socket 对象。
    失败抛出 ConnectionError。
    """
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect((ip, port))
        return sock
    except Exception as e:
        raise ConnectionError(f"连接 {ip}:{port} 失败: {e}") from e


def send_raw(sock: socket.socket, hex_str: str) -> int:
    """
    将 hex 字符串转为字节后通过 socket 发送。
    返回实际发送字节数，失败抛出异常。
    """
    data = binascii.unhexlify(hex_str)
    return sock.send(data)


def recv_once(sock: socket.socket, bufsize: int = RECV_BUFSIZE) -> bytes:
    """单次阻塞接收，返回原始字节。"""
    return sock.recv(bufsize)


def start_receive_loop(
    sock: socket.socket,
    on_packet: Callable[[bytes], None],
    on_error: Optional[Callable[[Exception], None]] = None,
    stop_event: Optional[threading.Event] = None,
) -> threading.Thread:
    """
    启动后台收包线程，持续 recv 并调用 on_packet。

    Args:
        sock:        已连接的 socket。
        on_packet:   接收到数据时的回调 (bytes -> None)。
        on_error:    发生异常时的回调（可选）。
        stop_event:  外部通过此 Event 通知线程退出（可选）。

    Returns:
        已启动的 daemon 线程。
    """
    if stop_event is None:
        stop_event = threading.Event()

    def _loop():
        sock.settimeout(1.0)  # 1 秒超时，便于检查 stop_event
        try:
            while not stop_event.is_set():
                try:
                    data = sock.recv(RECV_BUFSIZE)
                    if not data:
                        # 服务器主动断开（EOF）
                        if not stop_event.is_set() and on_error:
                            on_error(ConnectionResetError("服务器主动断开连接"))
                        break
                    try:
                        on_packet(data)
                    except Exception as cb_err:
                        print(f"[connector] on_packet 回调异常: {cb_err}")
                except socket.timeout:
                    continue
                except socket.error as se:
                    if not stop_event.is_set():
                        print(f"[connector] socket 错误: {se}")
                        if on_error:
                            on_error(se)
                    break
        except Exception as e:
            if on_error:
                on_error(e)
        finally:
            try:
                sock.close()
            except Exception:
                pass
            stop_event.set()

    t = threading.Thread(target=_loop, daemon=True, name="recv-loop")
    t.start()
    return t


def start_send_worker(
    send_queue: queue.PriorityQueue,
    get_sock: Callable[[], Optional[socket.socket]],
    send_lock: threading.Lock,
    stop_event: Optional[threading.Event] = None,
    interval: float = SEND_INTERVAL,
) -> threading.Thread:
    """
    启动发送队列工作线程。

    从 send_queue 取 (priority, hex_str) 元组，通过 get_sock() 获取当前 socket 发送。
    每包间隔 interval 秒（节流）。

    Args:
        send_queue:  PriorityQueue，元素为 (priority: int, hex_str: str)。
        get_sock:    返回当前活跃 socket（或 None）的可调用对象。
        send_lock:   发送锁，防止并发写 socket。
        stop_event:  外部停止信号。
        interval:    每包间隔秒数。

    Returns:
        已启动的 daemon 线程。
    """
    if stop_event is None:
        stop_event = threading.Event()

    def _worker():
        while not stop_event.is_set():
            try:
                priority, hex_str = send_queue.get(timeout=0.5)
            except queue.Empty:
                continue

            sock = get_sock()
            if not sock:
                print("[connector] send_worker: socket 不可用，丢弃包")
                send_queue.task_done()
                continue

            try:
                with send_lock:
                    send_raw(sock, hex_str)
                time.sleep(interval)
            except Exception as e:
                print(f"[connector] send_worker 发送失败: {e}")
            finally:
                send_queue.task_done()

    t = threading.Thread(target=_worker, daemon=True, name="send-worker")
    t.start()
    return t


def enqueue_packet(
    send_queue: queue.PriorityQueue,
    hex_str: str,
    priority: int = 10,
):
    """将 hex 报文加入发送队列。priority 越小越优先。"""
    send_queue.put((priority, hex_str))
