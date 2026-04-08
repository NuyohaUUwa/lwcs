"""
协议编解码层。
从 main-000.py 提取并重新实现所有报文拼装与解析函数。
**本文件不依赖 tkinter，不依赖 Flask，纯 stdlib。**
"""

import binascii
import re
import struct
from typing import List, Dict, Any, Optional


# ========================================================================= #
#  工具函数                                                                   #
# ========================================================================= #

def find_all_positions(text: str, pattern: str) -> List[int]:
    """在 hex 字符串中查找所有 pattern 出现位置（字符索引）。"""
    positions = []
    start = 0
    while True:
        pos = text.find(pattern, start)
        if pos == -1:
            break
        positions.append(pos)
        start = pos + 1
    return positions


def extract_utf8_segments(hex_str: str, min_len: int = 3) -> str:
    """
    从 hex 字符串的正文区域（[8:]，即去掉 4 字节长度头后的部分）逐字节扫描，
    提取所有合法 UTF-8 序列（≥ min_len 字节）。
    非 UTF-8 字节段用 [HEX:xx] 占位。

    返回拼接后的可读字符串。
    """
    try:
        body_bytes = bytes.fromhex(hex_str[16:])  # 跳过8字节帧头（16个hex字符）
    except (ValueError, binascii.Error):
        return ""

    result_parts: List[str] = []
    i = 0
    n = len(body_bytes)

    while i < n:
        # 尝试从 i 开始解码一段 UTF-8
        decoded_len = 0
        buf = bytearray()
        j = i
        while j < n:
            b = body_bytes[j]
            # 判断 UTF-8 首字节
            if b < 0x80:
                seq_len = 1
            elif b < 0xC0:
                # 续字节，单独出现则非法
                break
            elif b < 0xE0:
                seq_len = 2
            elif b < 0xF0:
                seq_len = 3
            else:
                seq_len = 4

            if j + seq_len > n:
                break

            seq = body_bytes[j:j + seq_len]
            try:
                ch = seq.decode('utf-8')
                buf += seq
                j += seq_len
                decoded_len += seq_len
            except UnicodeDecodeError:
                break

        if decoded_len >= min_len:
            text = buf.decode('utf-8', errors='replace')
            # 过滤控制字符（保留换行）
            text = re.sub(r'[\x00-\x08\x0b-\x1f\x7f]', '', text)
            if text.strip():
                result_parts.append(text)
            i = j
        else:
            # 非 UTF-8 字节，占位
            result_parts.append(f"[{body_bytes[i]:02X}]")
            i += 1

    return "".join(result_parts)


# ========================================================================= #
#  登录包                                                                     #
# ========================================================================= #

def generate_login_packet(account: str, password: str) -> str:
    """
    生成登录请求 hex 字符串。
    格式：4字节小端长度 + 固定前缀 + 账号长度 + 账号 + 密码长度 + 密码 + 结尾。
    """
    prefix = 'e80301007627baf1f5058f2700001c0000004001e0011600000'
    acc_len_hex = hex(len(account))[2:]
    pwd_len_hex = hex(len(password))[2:]
    acc_hex = account.encode("ASCII").hex()
    pwd_hex = password.encode("ASCII").hex()
    body = prefix + acc_len_hex + '00' + acc_hex + '0' + pwd_len_hex + '00' + pwd_hex + '0000'
    body_bytes = bytes.fromhex(body)
    length_hex = hex(len(body_bytes))[2:]
    return length_hex + '000000' + body


def get_session_id_hex(response_bytes: bytes) -> str:
    """
    从登录响应字节中提取 session_id（4字节 hex 字符串）。
    通过锚点 0x33 0x32（ASCII "32"）定位。
    """
    idx = response_bytes.find(b'\x33\x32')
    if idx == -1:
        raise RuntimeError("登录响应中未找到 0x3332 锚点，无法提取 session_id")
    session_bytes = response_bytes[idx + 2: idx + 2 + 4]
    return session_bytes.hex()


# ========================================================================= #
#  通用帧解析                                                                 #
# ========================================================================= #

def extract_packet_fingerprint(packet_hex: str) -> str:
    """返回报文指纹：hex 字符串[8:20]，用于快速分类下行报文。"""
    return packet_hex[8:20] if len(packet_hex) >= 20 else packet_hex


def extract_packet_content(hex_data: str) -> Dict[str, Any]:
    """
    解析报文帧头（content_length + command）并尽力提取 UTF-8 正文。
    返回 dict，失败时含 'error' 键。
    """
    try:
        byte_data = bytes.fromhex(hex_data)
        if len(byte_data) < 8:
            return {"error": "报文长度不足8字节"}

        content_length = struct.unpack('<I', byte_data[0:4])[0]
        command = struct.unpack('<I', byte_data[4:8])[0]

        actual_content = byte_data[8:8 + content_length] if len(byte_data) >= 8 + content_length else byte_data[8:]
        decoded = actual_content.decode('utf-8', errors='ignore')
        cleaned = re.sub(r'[\x00-\x08\x0b-\x1f\x7f]', '', decoded)

        # 判断报文类型
        packet_type = _classify_packet(command, cleaned, hex_data)

        # 解析服务器列表（仅登录响应）
        server_list = []
        if packet_type == "login_response" and ('通服' in decoded or '空山龙吟' in decoded):
            server_list = parse_server_list(decoded)

        return {
            "content_length": content_length,
            "command": command,
            "command_hex": f"0x{command:08X}",
            "cleaned_content": cleaned.strip(),
            "raw_decoded": decoded,
            "packet_type": packet_type,
            "server_list": server_list,
        }
    except Exception as e:
        return {"error": f"解析失败: {e}"}


def _classify_packet(command: int, content: str, hex_data: str) -> str:
    """根据命令字和内容关键词判断报文类型。"""
    announcement_keywords = [
        "双旦活动公告", "一月活动", "1月活动", "新年活动", "春节活动",
        "元旦活动", "维护公告", "群号：290172032", "通服", "空山龙吟",
    ]
    if command == 0x000103E8:
        for kw in announcement_keywords:
            if kw in content:
                return "login_response"
        if "系统公告" in content:
            return "system_announcement"
    if command == 0x000103D6:
        return "backpack_info"
    if command == 0x000103F2 or "【世】" in content:
        return "world_chat"
    if command == 0x000203E8 and any(k in content for k in ("姓名", "角色", "等级")):
        return "role_list"
    if command == 0x000203EE:
        return "role_select"
    if (command & 0xFFFF) == 0x08BF:
        return "role_info"
    return "other"


# ========================================================================= #
#  角色列表                                                                   #
# ========================================================================= #

def generate_role_list_packet(session_id: str, server_ip: str, server_port: int) -> str:
    """
    生成获取角色列表的请求 hex。
    根据服务器类型（龙一/龙二）选用不同模板，替换 session_id 占位符。
    """
    is_long_yi = 'tlz.shuihl.cn' in server_ip or server_port > 12060
    if is_long_yi:
        body = '1a000000e8030200eb038807f605f0030000080000005537ae260b000000'
        return body.replace("5537ae26", session_id)
    else:
        body = '1a000000e8030200eb031902f605f003000008000000e8c6f22301000000'
        return body.replace("e8c6f223", session_id)


def get_role_job_hex(section: str, full_res: str) -> str:
    """从角色段 hex 中提取职业字段 hex。"""
    marker = 'e8818ce4b89aefbc9a'  # "职业："的 UTF-8 hex
    idx = full_res.find(marker, full_res.find(section))
    if idx == -1:
        return ''
    after = full_res[idx + len(marker):]
    end = after.find('2f')  # '/' 分隔符
    return after[:end] if end != -1 else after[:20]


def parse_role_data(res_hex: str) -> Dict[str, Any]:
    """
    从角色列表响应 hex 中解析所有角色信息。
    返回 {'roleSize': int, 'userList': [{'role_id', 'role_name_cn', 'role_job', 'role_index'}, ...]}
    """
    role_size = res_hex.count('e5a793e5908defbc9a')  # "姓名："
    if role_size == 0:
        return {'roleSize': 0, 'userList': []}

    pattern = r'(e5a793e5908defbc9a)(.*?)(2fe5a3b0)'
    matches = re.findall(pattern, res_hex)

    user_list = []
    for i, match in enumerate(matches):
        section = match[1]
        start_pos = res_hex.find(section)
        role_id = res_hex[start_pos - 12:start_pos - 6] if start_pos >= 12 else f'role_{i}'
        try:
            role_name = bytes.fromhex(section).decode('utf-8')
        except Exception:
            role_name = f"角色{i + 1}"
        try:
            job_hex = get_role_job_hex(section, res_hex[start_pos:])
            role_job = bytes.fromhex(job_hex).decode('utf-8') if job_hex else "未知"
        except Exception:
            role_job = "未知"
        user_list.append({
            'role_id': role_id,
            'role_name_cn': role_name,
            'role_job': role_job,
            'role_index': i,
        })

    return {'roleSize': role_size, 'userList': user_list}


# ========================================================================= #
#  选角包                                                                     #
# ========================================================================= #

def generate_select_role_packet(role_id: str) -> str:
    """生成选角请求 hex，替换 role_id 占位符（6位 hex）。"""
    body = '18000000e8030200ee03a3fbf505f103000006000000485302000000'
    return body.replace("485302", role_id)


# 进入游戏后发送的附加固定包
ENTER_GAME_EXTRA_PACKET = '21000000e8030a000904cf07f605150400000f000000000000000000000400333432330000'


# ========================================================================= #
#  服务器列表解析                                                              #
# ========================================================================= #

def parse_server_list(content: str) -> List[Dict[str, Any]]:
    """从登录响应正文中解析服务器列表。"""
    cleaned = re.sub(r'[\x00-\x1f\x7f]', '', content)
    group_pos = cleaned.find('群号：290172032')
    server_content = cleaned[group_pos + len('群号：290172032'):] if group_pos != -1 else cleaned

    servers = []
    server_starts = list(re.finditer(r'(通服\d+|空山龙吟|生死符|新服|测试服|龙一|龙二)', server_content))

    for start in server_starts:
        start_pos = start.start()
        addr_pattern = re.compile(r'(tl[\d\w]+\.shuihl\.cn:|tl[\d\w]+\.shuihl:)', re.IGNORECASE)
        addr_match = addr_pattern.search(server_content, start_pos)
        if not addr_match:
            continue
        addr_pos = addr_match.start()
        domain = addr_match.group(0)[:-1]  # 去掉末尾冒号

        full_name = re.sub(r'[@?>=<]+$', '', server_content[start_pos:addr_pos]).strip()
        full_name = re.sub(r'\s+', '', full_name)
        full_name = re.sub(r'[^\u4e00-\u9fa5\d\(\)\-]+', '', full_name).strip()

        domain_end = addr_match.end()
        port_match = re.match(r'(\d+)', server_content[domain_end:])
        if not port_match:
            continue
        port = int(port_match.group(1))
        port_end = domain_end + len(port_match.group(1))

        srv_match = re.search(r'(srv\d{3})', server_content[port_end:port_end + 20])
        if not srv_match:
            continue
        srv_id = srv_match.group(1)

        if full_name:
            servers.append({
                'name': full_name,
                'ip': domain,
                'port': port,
                'id': srv_id,
                'status': 'online',
            })

    # 去重
    unique: Dict[str, Any] = {}
    for srv in servers:
        key = srv['name'].lower()
        if key not in unique:
            unique[key] = srv
    return list(unique.values())
