"""
角色业务协议。
只负责角色列表/选角相关报文构造与响应解析。
"""

import re
from typing import Any, Dict

from utils.random_num import random_num_hex4

ENTER_GAME_EXTRA_PACKET_TEMPLATE = (
    "24000000e80303003d28{random_1}f6054c28000012000000000000000000000000000000000000000000"
    "14000000e80317000d30{random_2}f6050c300000020000000a00"
    "12000000e80302000504{random_3}f5050204000000000000"
)


def build_enter_game_extra_packet() -> str:
    return ENTER_GAME_EXTRA_PACKET_TEMPLATE.format(
        random_1=random_num_hex4(),
        random_2=random_num_hex4(),
        random_3=random_num_hex4(),
    )


def build_role_list_packet(session_id: str, server_ip: str, server_port: int) -> str:
    """构造获取角色列表请求。"""
    is_long_yi = "tlz.shuihl.cn" in server_ip or server_port > 12060
    if is_long_yi:
        body = "1a000000e8030200eb038807f605f0030000080000005537ae260b000000"
        return body.replace("5537ae26", session_id)
    body = "1a000000e8030200eb031902f605f003000008000000e8c6f22301000000"
    return body.replace("e8c6f223", session_id)


def build_select_role_packet(role_id: str) -> str:
    """构造选角请求。"""
    return "18000000e8030200ee03a3fbf505f103000006000000485302000000".replace("485302", role_id)


def _get_role_job_hex(section: str, full_res: str) -> str:
    marker = "e8818ce4b89aefbc9a"
    idx = full_res.find(marker, full_res.find(section))
    if idx == -1:
        return ""
    after = full_res[idx + len(marker) :]
    end = after.find("2f")
    return after[:end] if end != -1 else after[:20]


def parse_role_data(res_hex: str) -> Dict[str, Any]:
    """解析角色列表响应。"""
    role_size = res_hex.count("e5a793e5908defbc9a")
    if role_size == 0:
        return {"roleSize": 0, "userList": []}

    matches = re.findall(r"(e5a793e5908defbc9a)(.*?)(2fe5a3b0)", res_hex)
    user_list = []
    for i, match in enumerate(matches):
        section = match[1]
        start_pos = res_hex.find(section)
        role_id = res_hex[start_pos - 12 : start_pos - 6] if start_pos >= 12 else f"role_{i}"
        try:
            role_name = bytes.fromhex(section).decode("utf-8")
        except Exception:
            role_name = f"角色{i + 1}"
        try:
            job_hex = _get_role_job_hex(section, res_hex[start_pos:])
            role_job = bytes.fromhex(job_hex).decode("utf-8") if job_hex else "未知"
        except Exception:
            role_job = "未知"
        user_list.append(
            {
                "role_id": role_id,
                "role_name_cn": role_name,
                "role_job": role_job,
                "role_index": i,
            }
        )

    return {"roleSize": role_size, "userList": user_list}


def parse_select_role_response(response_bytes: bytes) -> Dict[str, Any]:
    if not response_bytes:
        return {"ok": False, "text": "", "raw_hex": ""}
    text = response_bytes.decode("utf-8", errors="ignore")
    normalized = text.strip()
    return {
        "ok": "登录异常" not in normalized and "请重新登录" not in normalized,
        "text": normalized,
        "raw_hex": response_bytes.hex(),
    }
