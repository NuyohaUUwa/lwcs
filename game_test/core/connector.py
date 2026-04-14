"""
统一网络 I/O 入口。
任何业务模块都只能通过本模块建立连接、发送报文、接收报文和启动运行时线程。
"""

import binascii
import queue
import socket
import threading
import time
from typing import Callable, Optional

from config import RECV_BUFSIZE, SEND_INTERVAL
from core.codec import split_game_frame_bytes
from core.session import get_session
from features.packet_probe import record_packet

_F703_PACKET_MARK = "e8030500f703"
_BATTLE_WAITING_STATES = {"waiting_start_response", "waiting_action_result"}


def _get_connected_socket() -> socket.socket:
    session = get_session()
    sock = session.sock
    if not sock:
        raise ConnectionError("socket 不可用")
    return sock


def _send_all(sock: socket.socket, data: bytes) -> int:
    total_sent = 0
    while total_sent < len(data):
        sent = sock.send(data[total_sent:])
        if sent <= 0:
            raise ConnectionError("socket 发送失败")
        total_sent += sent
    return total_sent


def open_connection(ip: str, port: int, timeout: float = 15.0) -> socket.socket:
    """建立 TCP 连接并写入 session。"""
    session = get_session()
    close_connection()
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect((ip, port))
        session.sock = sock
        session.server_ip = ip
        session.server_port = port
        session.recv_framing_buffer = b""
        return sock
    except Exception as e:
        raise ConnectionError(f"连接 {ip}:{port} 失败: {e}") from e


def close_connection():
    """关闭当前连接。"""
    session = get_session()
    sock = session.sock
    session.sock = None
    if sock:
        try:
            sock.close()
        except Exception:
            pass


def send_packet(hex_str: str, priority: int = 10, use_queue: bool = True) -> int:
    """
    统一发包入口。
    use_queue=True 时仅入队；否则立即发送并返回实际字节数。
    """
    session = get_session()
    clean_hex = hex_str.lower().replace(" ", "")
    if use_queue:
        session.send_queue.put((priority, clean_hex))
        return 0

    data = binascii.unhexlify(clean_hex)
    with session._send_lock:
        sock = _get_connected_socket()
        sent_bytes = _send_all(sock, data)
    if sent_bytes > 0:
        record_packet(data, "UP")
    return sent_bytes


def send_and_receive_once(
    packet_hex: str,
    recv_timeout: float = 5.0,
    bufsize: int = RECV_BUFSIZE,
) -> bytes:
    """在当前连接上发送一次并同步接收一次响应。"""
    sock = _get_connected_socket()
    session = get_session()
    old_timeout = sock.gettimeout()
    try:
        sock.settimeout(recv_timeout)
        send_packet(packet_hex, use_queue=False)
        chunk = sock.recv(bufsize) or b""
        combined = session.recv_framing_buffer + chunk
        frames, rest = split_game_frame_bytes(combined)
        session.recv_framing_buffer = rest
        for frame in frames:
            record_packet(frame, "DN")
        return b"".join(frames)
    finally:
        sock.settimeout(old_timeout)


def connect_and_exchange(
    ip: str,
    port: int,
    packet_hex: str,
    *,
    connect_timeout: float = 15.0,
    recv_timeout: float = 5.0,
    bufsize: int = RECV_BUFSIZE,
    keep_open: bool = False,
) -> bytes:
    """
    打开连接、发送一次、接收一次。
    keep_open=True 时连接保留给后续流程；否则自动关闭。
    返回值仅为拼帧后的完整报文字节串拼接，不含 TCP 半包；半包写入 recv_framing_buffer（仅 keep_open 时保留）。
    """
    sock = open_connection(ip, port, timeout=connect_timeout)
    session = get_session()
    try:
        old_timeout = sock.gettimeout()
        sock.settimeout(recv_timeout)
        send_packet(packet_hex, use_queue=False)
        chunk = sock.recv(bufsize) or b""
        combined = session.recv_framing_buffer + chunk
        frames, rest = split_game_frame_bytes(combined)
        if keep_open:
            session.recv_framing_buffer = rest
        else:
            session.recv_framing_buffer = b""
        for frame in frames:
            record_packet(frame, "DN")
        response = b"".join(frames)
        sock.settimeout(old_timeout)
        if not keep_open:
            close_connection()
        return response
    except Exception:
        if not keep_open:
            close_connection()
        raise


def start_connection_runtime(
    on_packet: Callable[[bytes], None],
    on_disconnect: Optional[Callable[[Exception], None]] = None,
    *,
    interval: float = SEND_INTERVAL,
) -> dict:
    """启动统一收包/发包线程。"""
    session = get_session()
    sock = _get_connected_socket()
    stop_event = threading.Event()
    session.connection_stop_event = stop_event

    recv_thread = start_receive_loop(
        sock=sock,
        on_packet=on_packet,
        on_error=on_disconnect,
        stop_event=stop_event,
    )
    send_thread = start_send_worker(
        send_queue=session.send_queue,
        send_lock=session._send_lock,
        stop_event=stop_event,
        interval=interval,
    )
    session.recv_thread = recv_thread
    session.send_thread = send_thread
    return {"recv_thread": recv_thread, "send_thread": send_thread, "stop_event": stop_event}


def stop_connection_runtime():
    session = get_session()
    stop_event = session.connection_stop_event
    if stop_event:
        stop_event.set()
    close_connection()
    # 旧发包线程已停止，但队列仍可能留有断线前的 f703/f603 等；不重置则重连后会批量发出
    session.discard_send_queue()
    session.clear_connection_runtime()


def start_receive_loop(
    sock: socket.socket,
    on_packet: Callable[[bytes], None],
    on_error: Optional[Callable[[Exception], None]] = None,
    stop_event: Optional[threading.Event] = None,
) -> threading.Thread:
    """启动后台收包线程。"""
    if stop_event is None:
        stop_event = threading.Event()

    def _loop():
        sock.settimeout(1.0)
        session = get_session()
        try:
            while not stop_event.is_set():
                try:
                    chunk = sock.recv(RECV_BUFSIZE)
                    if not chunk:
                        if not stop_event.is_set() and on_error:
                            on_error(ConnectionResetError("服务器主动断开连接"))
                        break
                    combined = session.recv_framing_buffer + chunk
                    frames, rest = split_game_frame_bytes(combined)
                    session.recv_framing_buffer = rest
                    for data in frames:
                        record_packet(data, "DN")
                        try:
                            on_packet(data)
                        except Exception as cb_err:
                            print(f"[connector] on_packet 回调异常: {cb_err}")
                except socket.timeout:
                    continue
                except socket.error as se:
                    if not stop_event.is_set() and on_error:
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

    thread = threading.Thread(target=_loop, daemon=True, name="recv-loop")
    thread.start()
    return thread


def start_send_worker(
    send_queue: queue.PriorityQueue,
    send_lock: threading.Lock,
    stop_event: Optional[threading.Event] = None,
    interval: float = SEND_INTERVAL,
) -> threading.Thread:
    """启动后台发包线程。"""
    if stop_event is None:
        stop_event = threading.Event()

    def _should_drop_queued_packet(hex_str: str) -> bool:
        packet_hex = str(hex_str or "").lower()
        if _F703_PACKET_MARK not in packet_hex:
            return False
        session = get_session()
        with session._lock:
            battle_state = str(session.battle_state or "")
        if battle_state in _BATTLE_WAITING_STATES:
            return False
        return True

    def _worker():
        while not stop_event.is_set():
            try:
                priority, hex_str = send_queue.get(timeout=0.5)
            except queue.Empty:
                continue

            try:
                if _should_drop_queued_packet(hex_str):
                    continue
                data = binascii.unhexlify(hex_str)
                with send_lock:
                    if _should_drop_queued_packet(hex_str):
                        continue
                    sock = _get_connected_socket()
                    sent_bytes = _send_all(sock, data)
                if sent_bytes > 0:
                    record_packet(data, "UP")
                time.sleep(interval)
            except Exception as e:
                print(f"[connector] send_worker 发送失败: {e}")
            finally:
                send_queue.task_done()

    thread = threading.Thread(target=_worker, daemon=True, name="send-worker")
    thread.start()
    return thread
