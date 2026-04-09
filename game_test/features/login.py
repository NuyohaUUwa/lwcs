"""
登录业务协议。
只负责登录报文构造和登录响应解析。
"""

import re
from typing import Any, Dict, List

from core.codec import parse_frame


def build_login_packet(account: str, password: str) -> str:
    """构造登录请求报文。"""
    prefix = "e80301007627baf1f5058f2700001c0000004001e0011600000"
    acc_len_hex = hex(len(account))[2:]
    pwd_len_hex = hex(len(password))[2:]
    acc_hex = account.encode("ASCII").hex()
    pwd_hex = password.encode("ASCII").hex()
    body = prefix + acc_len_hex + "00" + acc_hex + "0" + pwd_len_hex + "00" + pwd_hex + "0000"
    body_bytes = bytes.fromhex(body)
    length_hex = hex(len(body_bytes))[2:]
    return length_hex + "000000" + body


def extract_session_id(response_bytes: bytes) -> str:
    """从登录响应中提取 session_id。"""
    idx = response_bytes.find(b"\x33\x32")
    if idx == -1:
        raise RuntimeError("登录响应中未找到 0x3332 锚点，无法提取 session_id")
    return response_bytes[idx + 2 : idx + 6].hex()


def parse_server_list(content: str) -> List[Dict[str, Any]]:
    """从登录响应正文中提取服务器列表。"""
    cleaned = re.sub(r"[\x00-\x1f\x7f]", "", content)
    group_pos = cleaned.find("群号：290172032")
    server_content = cleaned[group_pos + len("群号：290172032") :] if group_pos != -1 else cleaned
    servers = []
    server_starts = list(re.finditer(r"(通服\d+|空山龙吟|生死符|新服|测试服|龙一|龙二)", server_content))
    for start in server_starts:
        start_pos = start.start()
        addr_pattern = re.compile(r"(tl[\d\w]+\.shuihl\.cn:|tl[\d\w]+\.shuihl:)", re.IGNORECASE)
        addr_match = addr_pattern.search(server_content, start_pos)
        if not addr_match:
            continue
        addr_pos = addr_match.start()
        domain = addr_match.group(0)[:-1]
        full_name = re.sub(r"[@?>=<]+$", "", server_content[start_pos:addr_pos]).strip()
        full_name = re.sub(r"\s+", "", full_name)
        full_name = re.sub(r"[^\u4e00-\u9fa5\d\(\)\-]+", "", full_name).strip()
        domain_end = addr_match.end()
        port_match = re.match(r"(\d+)", server_content[domain_end:])
        if not port_match:
            continue
        port = int(port_match.group(1))
        port_end = domain_end + len(port_match.group(1))
        srv_match = re.search(r"(srv\d{3})", server_content[port_end : port_end + 20])
        if not srv_match:
            continue
        if full_name:
            servers.append(
                {
                    "name": full_name,
                    "ip": domain,
                    "port": port,
                    "id": srv_match.group(1),
                    "status": "online",
                }
            )

    unique = {}
    for srv in servers:
        unique.setdefault(srv["name"].lower(), srv)
    return list(unique.values())


def parse_login_response(response_bytes: bytes) -> Dict[str, Any]:
    """解析登录响应，返回 session_id、公告与服务器列表。"""
    session_id = extract_session_id(response_bytes)
    parsed = parse_frame(response_bytes.hex())
    announcement = parsed.get("cleaned_content", "")
    return {
        "session_id": session_id,
        "announcement": announcement,
        "server_list": parse_server_list(parsed.get("raw_decoded", "")),
        "frame": parsed,
    }
