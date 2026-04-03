import queue
import random
import re
import struct
import tkinter as tk
from tkinter import ttk, messagebox
import threading
import sys
import os
import socket
import binascii
import base64
import time

# 添加项目根目录到sys.path，以便导入其他模块
sys.path.append(os.path.dirname(os.path.abspath(__file__)))


def parse_treasure_box_info(content):
    """
    解析宝箱相关信息，特别查找高级宝箱的数量
    """
    treasure_info = {}

    # 查找宝箱相关关键词
    treasure_keywords = ['宝箱', '箱子', '礼盒', '礼包', '宝盒']

    for keyword in treasure_keywords:
        if keyword in content:
            # 查找该关键词后面的数字
            pattern = rf'{keyword}[\s\W]*(\d+)'
            matches = re.findall(pattern, content)
            if matches:
                treasure_info[keyword] = [int(num) for num in matches]

    # 特别查找高级宝箱
    advanced_box_patterns = [
        r'高级宝箱\s*[X×]\s*(\d+)',
        r'高级宝箱\s*(\d+)\s*个',
        r'高级宝箱\s*(\d+)',
        r'(\d+)\s*个\s*高级宝箱'
    ]

    for pattern in advanced_box_patterns:
        matches = re.findall(pattern, content, re.IGNORECASE)
        if matches:
            treasure_info['高级宝箱'] = [int(num) for num in matches]
            break

    return treasure_info


def connect_game_server(session_id, server_ip, server_port, role_id, role_index=0, sock=None):
    """
    连接游戏服务器并选择角色，返回连接的socket对象
    使用同一个socket对象完成从获取角色列表到进入游戏的整个过程

    Args:
        session_id (str): 会话ID
        server_ip (str): 游戏服务器IP
        server_port (int): 游戏服务器端口
        role_id (str): 角色ID
        role_index (int): 角色索引
        sock (socket, optional): 已有的socket对象

    Returns:
        socket: 连接的socket对象，失败返回None
    """
    print(
        f"connect_game_server被调用，参数: session_id={session_id}, server_ip={server_ip}, server_port={server_port}, role_id={role_id}, role_index={role_index}, sock={sock}")

    try:
        role_list = None
        # 1. 连接到游戏服务器并获取角色列表
        print(f"步骤1: 连接到游戏服务器并获取角色列表...")
        role_list, sock = connect_game_server_get_roles(session_id, server_ip, server_port, sock)

        if not sock or 'error' in role_list:
            print(f"步骤1失败: {role_list.get('error', '获取角色列表失败')}")
            if sock:
                sock.close()
            return None

        print(f"步骤1成功: 成功获取角色列表，socket对象: {sock}")

        # 2. 使用同一个socket发送选择角色请求
        print(f"步骤2: 使用同一个socket发送选择角色请求...")

        # 设置套接字超时
        sock.settimeout(5)  # 设置5秒超时
        print(f"成功设置套接字超时为5秒")

        # 使用用户提供的正确选择角色请求格式
        body = '18000000e8030200ee03a3fbf505f103000006000000485302000000'
        print(f"原始选择角色请求: {body}")

        # 替换角色ID
        body = body.replace("485302", role_id)
        print(f"替换角色ID后的请求: {body}")

        packet = binascii.unhexlify(body)
        print(f"转换为字节后的请求: {packet.hex()}")

        sent = sock.send(packet)
        print(f"成功发送选择角色请求，发送字节数: {sent}")

        # 3. 接收角色选择响应
        print("步骤3: 接收角色选择响应...")
        response = sock.recv(4048)
        print(f"成功接收到角色选择响应，长度: {len(response)} 字节")
        print(f"角色选择响应内容: {response.hex()}")

        # 解析响应内容
        try:
            content = extract_packet_content(response.hex())
            cleaned_content = content.get('cleaned_content', '无法解析内容')
            packet_type = content.get('packet_type', 'other')
            print(f"解析角色选择响应: {cleaned_content}")

            # 检查是否为登录异常
            if '登录异常' in cleaned_content or '请重新登录' in cleaned_content:
                print(f"检测到登录异常响应: {cleaned_content}")
                print("关闭连接并返回None")
                sock.close()
                return None

            # 如果是角色选择响应，打印相关信息
            if packet_type == 'role_selection' and cleaned_content:
                print(f"收到角色选择响应: {cleaned_content}")
        except Exception as parse_error:
            print(f"解析角色选择响应失败: {str(parse_error)}")

        # 4. 获取角色名称并打印欢迎信息
        user_info = role_list['userList'][role_index]
        print(f"欢迎您!!! {user_info['role_name_cn']}")

        # 5. 设置为1秒超时，便于后续接收循环
        sock.settimeout(1)
        print(f"成功设置套接字超时为1秒，准备持续接收报文")

        # 不关闭连接，返回活跃的socket对象
        print("步骤4: 成功进入游戏，返回活跃的socket对象")
        return sock
    except socket.timeout as timeout_error:
        print(f"连接游戏服务器超时: {str(timeout_error)}")
        if sock:
            sock.close()
        return None
    except socket.error as sock_error:
        print(f"套接字错误: {str(sock_error)}")
        if sock:
            sock.close()
        return None
    except Exception as e:
        print(f"选择角色失败: {str(e)}")
        import traceback
        traceback.print_exc()
        if sock:
            sock.close()
        return None


def connect_game_server_get_roles(session_id, server_ip, server_port, sock=None):
    """
    连接游戏服务器并获取角色列表
    使用传入的socket对象或创建新的socket

    Args:
        session_id (str): 会话ID
        server_ip (str): 游戏服务器IP
        server_port (int): 游戏服务器端口
        sock (socket, optional): 已有的socket对象

    Returns:
        tuple: (角色列表信息或错误信息, socket对象或None)
    """
    try:
        # 如果没有传入socket对象，创建新的socket
        if not sock:
            print(f"没有传入socket对象，创建新的socket...")
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(15)  # 设置15秒超时
            sock.connect((server_ip, server_port))
            print(f"新创建的socket对象: {sock}")
        else:
            print(f"使用传入的socket对象: {sock}")
            sock.settimeout(15)  # 也给传入的socket设置超时

        # 根据不同的游戏服务器使用不同的角色列表请求报文格式
        # 判断服务器类型：根据服务器IP或域名判断是龙一服还是龙二服
        # 龙一服的游戏服务器域名包含tlz，而龙二服的游戏服务器域名包含tl10、tl11等
        is_long_yi_server = 'tlz.shuihl.cn' in server_ip  # 龙一服的游戏服务器域名包含tlz
        is_long_er_server = 'tl10.shuihl.cn' in server_ip or 'tl11.shuihl.cn' in server_ip  # 龙二服的游戏服务器域名包含tl10或tl11

        # 如果根据域名无法判断，则根据端口号判断：龙一服的游戏服务器端口通常大于12060，龙二服的游戏服务器端口为12001
        if not is_long_yi_server and not is_long_er_server:
            is_long_yi_server = server_port > 12060  # 龙一服的游戏服务器端口通常大于12060

        if is_long_yi_server:
            # 龙一服的角色列表请求报文格式
            body = '1a000000e8030200eb038807f605f0030000080000005537ae260b000000'
            # 替换body里的5537ae26为sessionid
            body = body.replace("5537ae26", session_id)
        else:
            # 龙二服的角色列表请求报文格式（用户提供的格式）
            body = '1a000000e8030200eb031902f605f003000008000000e8c6f22301000000'
            # 替换body里的e8c6f223为sessionid
            body = body.replace("e8c6f223", session_id)

        print(f"调试信息: 使用{'龙一服' if is_long_yi_server else '龙二服'}的角色列表请求报文格式")
        packet = binascii.unhexlify(body)
        sent = sock.send(packet)
        print(f"发送角色列表请求成功，发送字节数: {sent}")
        print(f"角色列表请求内容: {body}")

        # 接收角色列表响应
        response = sock.recv(10240)
        print(f"收到角色列表响应，长度: {len(response)} 字节")
        print(f"角色列表响应内容: {response.hex()}")

        if response:
            # 检查响应是否包含登录异常
            response_hex = response.hex()
            response_str = response.decode('utf-8', errors='ignore')

            if '登录异常' in response_str or '请重新登录' in response_str:
                print(f"调试信息: 检测到登录异常响应: {response_str}")
                sock.close()
                return {'error': '登录异常，请重新登录'}, None

            role_list = parse_role_data(response.hex())
            role_list['sessionId'] = session_id
            print(f"获取角色列表成功: {role_list}")
            # 不关闭连接，返回角色列表和socket对象
            return role_list, sock

        # 没有收到响应，关闭连接
        sock.close()
        return {'error': '没有收到角色列表响应'}, None
    except Exception as e:
        print(f"获取角色列表失败: {str(e)}")
        import traceback
        traceback.print_exc()
        if sock:
            sock.close()
        return {'error': f'获取角色列表失败: {str(e)}'}, None


def parse_server_list(content):
    """
    从内容中解析服务器列表
    服务器格式：名称 + 地址:端口 + 标识，用特殊字符分隔
    例如：通服6-虎丘寺(新)tlz.shuihl.cn:12065srv065@通服5-天台山(火)tlz.shuihl.cn:12064srv064
    """
    servers = []

    # 确保re模块可用
    import re

    # 首先尝试从cleaned_content中提取（因为decoded可能包含控制字符）
    cleaned = re.sub(r'[\x00-\x1f\x7f]', '', content)

    # 改进：先找到服务器列表的起始位置，跳过公告内容
    # 查找群号的位置，群号后面的内容就是服务器列表
    group_num_pos = cleaned.find('群号：290172032')
    if group_num_pos != -1:
        # 从群号后面开始提取服务器列表
        server_list_start = group_num_pos + len('群号：290172032')
        server_content = cleaned[server_list_start:]
    else:
        # 如果没找到群号，使用整个内容
        server_content = cleaned

    # 优化正则表达式，确保能匹配生死符服务器
    # 匹配模式：更灵活的模式，能够匹配不同的域名格式和服务器名称
    # 匹配服务器名称（包括生死符） + 域名:端口 + srv标识
    pattern = r'([^:]+?)(tl[\d\w]+\.shuihl\.cn|tl[\d\w]+\.shuihl):(\d+)(srv\d+)'
    matches = re.findall(pattern, server_content)

    # 如果没有匹配到，尝试使用更简单的模式
    if not matches:
        pattern = r'([^:]+?):(\d+)(srv\d+)'
        matches = re.findall(pattern, server_content)

    for match in matches:
        # 处理不同模式的匹配结果
        if len(match) == 4:
            # 完整模式：server_name, domain, port, srv_id
            server_name, domain, port, srv_id = match
            # 使用实际匹配到的域名，不再硬编码
            ip = domain
        elif len(match) == 3:
            # 简单模式：server_name, port, srv_id
            server_name, port, srv_id = match
            ip = 'tlz.shuihl.cn'  # 使用标准域名作为默认
        else:
            # 其他情况，跳过
            continue

        # 清理服务器名称：先移除所有无效字符，只保留中文、数字、括号、连字符和空格
        server_name = re.sub(r'[^\u4e00-\u9fa5\d\(\)\-\s]+', '', server_name)
        # 移除名称末尾的特殊字符
        server_name = re.sub(r'[@\?\>\=\<\/\^\$]+$', '', server_name).strip()
        # 移除名称中间的多余空格
        server_name = re.sub(r'\s+', '', server_name)

        # 确保服务器名称有效（不为空）
        if server_name:
            # 检查是否是生死符服务器
            if '生死符' in server_name:
                print(f"调试信息: 正则匹配到生死符服务器: {server_name} - {ip}:{port} - {srv_id}")
            servers.append({
                'name': server_name,
                'ip': ip,
                'port': int(port),
                'id': srv_id,
                'status': 'online'  # 默认在线状态
            })

    # 手动提取服务器列表，确保能正确处理生死符服务器
    # 清空之前的服务器列表，只使用手动提取的结果
    servers = []

    # 查找所有服务器开头的位置，确保包含生死符
    server_starts = list(re.finditer(r'(通服\d+|空山龙吟|生死符|新服|测试服|龙一|龙二)', server_content))

    print(
        f"调试信息: 手动提取服务器列表，server_starts: {[server_content[start.start():start.start() + 15] for start in server_starts]}")

    for i, start in enumerate(server_starts):
        start_pos = start.start()
        server_name_start = server_content[start_pos:start_pos + 30]
        print(f"调试信息: 处理服务器 {i + 1}，起始位置: {start_pos}，服务器名称开始部分: {server_name_start}")

        # 查找服务器地址的位置，支持多种域名格式
        # 支持 tlz.shuihl.cn: 和其他可能的域名格式
        addr_pattern = re.compile(r'(tl[\d\w]+\.shuihl\.cn:|tl[\d\w]+\.shuihl:)', re.IGNORECASE)
        addr_match = addr_pattern.search(server_content, start_pos)
        if not addr_match:
            print(f"调试信息: 在 {server_name_start} 附近未找到服务器地址")
            continue
        addr_pos = addr_match.start()
        domain = addr_match.group(0)
        print(f"调试信息: 找到服务器地址: {domain}，位置: {addr_pos}")
        print(f"调试信息: 服务器地址前后内容: {server_content[max(0, addr_pos - 20):addr_pos + 30]}")

        # 提取完整的服务器名称
        full_name = server_content[start_pos:addr_pos].strip()
        # 清理服务器名称
        full_name = re.sub(r'[@\?\>\=\<]+$', '', full_name).strip()
        full_name = re.sub(r'\s+', '', full_name)
        print(f"调试信息: 提取的服务器名称: {full_name}")

        # 提取端口号，动态计算域名后的位置
        # 首先获取完整的域名部分
        domain_end = addr_match.end()
        # 从域名结束位置开始提取端口号
        port_match = re.match(r'(\d+)', server_content[domain_end:])
        if not port_match:
            print(f"调试信息: 无法提取端口号")
            continue
        port = port_match.group(1)
        port_end = domain_end + len(port)
        print(f"调试信息: 提取的端口号: {port}")

        # 提取srv标识，确保只匹配srv后面跟着3位数字的格式
        srv_pattern = re.compile(r'(srv\d{3})')
        srv_match = srv_pattern.search(server_content[port_end:port_end + 20])
        if not srv_match:
            print(f"调试信息: 无法提取srv标识")
            continue
        srv_id = srv_match.group(1)
        print(f"调试信息: 提取的srv标识: {srv_id}")

        # 从domain中提取实际的域名（去掉冒号）
        actual_domain = domain[:-1]  # 去掉末尾的冒号
        print(f"调试信息: 提取的实际域名: {actual_domain}")

        # 检查是否是生死符服务器
        if '生死符' in full_name:
            print(f"调试信息: 检测到生死符服务器，完整信息: {full_name} - {actual_domain}:{port} - {srv_id}")

        # 确保服务器名称有效
        if full_name:
            servers.append({
                'name': full_name,
                'ip': actual_domain,
                'port': int(port),
                'id': srv_id,
                'status': 'online'
            })
            print(f"调试信息: 添加服务器到列表: {full_name} - {actual_domain}:{port} - {srv_id}")

    # 最终优化：只去重，不强制添加或删除服务器
    # 去重：创建一个字典，使用服务器名称的核心部分作为键，确保不重复
    unique_servers = {}
    for srv in servers:
        original_name = srv["name"].strip()

        # 1. 首先，只保留中文、数字、括号、连字符和空格
        # 移除所有其他字符，包括乱码
        cleaned_name = re.sub(r'[^\u4e00-\u9fa5\d\(\)\-\s]+', '', original_name)

        # 2. 去除前后空格
        cleaned_name = cleaned_name.strip()

        # 3. 去除名称中的多余空格
        cleaned_name = re.sub(r'\s+', ' ', cleaned_name)

        # 4. 提取服务器名称的核心部分
        core_pattern = r'(通服\d+|空山龙吟|生死符)'
        match = re.search(core_pattern, cleaned_name)

        if match:
            # 提取核心部分
            core_start = match.start()
            core_name = cleaned_name[core_start:]

            # 5. 再次清理核心名称，确保只包含有效字符
            final_name = core_name.strip()

            # 更新服务器对象的名称
            srv["name"] = final_name

            # 使用最终名称作为键
            key = final_name.lower()
            if key not in unique_servers:
                unique_servers[key] = srv
        else:
            # 如果没有找到核心关键词，使用清理后的完整名称
            if cleaned_name:
                # 更新服务器对象的名称
                srv["name"] = cleaned_name

                key = cleaned_name.lower()
                if key not in unique_servers:
                    unique_servers[key] = srv

    # 将去重后的服务器转换为列表
    servers = list(unique_servers.values())

    return servers


def start_receive_thread(sock, callback):
    """
    启动线程持续接收报文

    Args:
        sock (socket): 连接的socket对象
        callback (function): 处理接收到的报文的回调函数

    Returns:
        threading.Thread: 启动的线程对象
    """

    def receive_loop():
        try:
            sock.settimeout(1)  # 设置1秒超时，便于检查线程是否需要退出
            while True:
                try:
                    # 接收报文
                    response = sock.recv(14048)
                    if response:
                        # 直接调用回调函数处理报文，不打印日志
                        try:
                            callback(response)
                        except Exception as callback_error:
                            # 只打印错误信息，不打印其他调试信息
                            print(f"回调函数处理报文失败: {str(callback_error)}")
                    else:
                        # 服务器关闭连接
                        break
                except socket.timeout:
                    # 超时，继续循环，不打印日志
                    continue
                except socket.error as sock_error:
                    # 只打印错误信息，不打印其他调试信息
                    print(f"套接字错误: {str(sock_error)}")
                    break
        except Exception as e:
            # 只打印错误信息，不打印其他调试信息
            print(f"接收报文线程异常: {str(e)}")
        finally:
            # 关闭连接
            try:
                sock.close()
            except:
                pass

    # 创建并启动线程
    thread = threading.Thread(target=receive_loop, daemon=True)
    thread.start()
    return thread


def connect_game_server_select_role(session_id, server_ip, server_port, role_id):
    """
    连接游戏服务器并选择角色（兼容旧版调用）

    Args:
        session_id (str): 会话ID
        server_ip (str): 游戏服务器IP
        server_port (int): 游戏服务器端口
        role_id (str): 角色ID

    Returns:
        bool: 连接是否成功
    """
    try:
        # 连接到游戏服务器
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.connect((server_ip, server_port))

        # 发送选择角色请求
        # 使用与获取角色列表相同的请求格式，因为该格式已知有效
        body = '1a000000e8030200eb038807f605f0030000080000005537ae260b000000'
        body = body.replace("5537ae26", session_id)
        packet = binascii.unhexlify(body)
        sent = sock.send(packet)

        # 接收角色选择响应
        response = sock.recv(4048)
        sock.close()

        print(f"角色选择成功: {response.hex()}")
        return True
    except Exception as e:
        print(f"选择角色失败: {str(e)}")
        return False


def get_role_job(section, res):
    """
    解析角色列表数据
    支持多种响应格式，包括有角色和无角色的情况
    """
    res = res[res.find('e8818ce4b89aefbc9a') + len('e8818ce4b89aefbc9a'):]

    return res[:res.find('2f')]
    pass


def parse_role_data(res):
    """
    解析角色列表数据
    支持多种响应格式，包括有角色和无角色的情况
    """
    print(f"调试信息: parse_role_data收到的原始数据: {res}")

    # 首先尝试解码响应内容，看看是否包含中文提示
    try:
        # 转换为字节
        byte_data = bytes.fromhex(res)
        # 尝试解码中文
        decoded = byte_data.decode('utf-8', errors='ignore')
        print(f"调试信息: 解码后的响应内容: {decoded}")
    except Exception as e:
        print(f"调试信息: 解码响应内容失败: {str(e)}")

    # 查询有几个e5a793e5908defbc9a 姓名：
    role_size = res.count('e5a793e5908defbc9a')
    print(f"调试信息: 找到 '姓名：' 的数量: {role_size}")

    if role_size == 0:
        # 检查是否包含其他角色相关的关键词
        if 'e8a792e889b2' in res:  # '角色'的hex
            print(f"调试信息: 响应包含角色相关关键词，但未找到 '姓名：' 字段")
        return {'roleSize': 0, 'userList': []}

    # 找到body中  每一个  从 e5a793e5908defbc9a后面开始截取到第一次出现2fe5a3b0前的报文
    # 使用正则表达式找到所有从"姓名："到"/声"之间的内容
    pattern = r'(e5a793e5908defbc9a)(.*?)(2fe5a3b0)'
    matches = re.findall(pattern, res)
    print(f"调试信息: 正则表达式匹配到的角色数量: {len(matches)}")

    sections = []
    role_list_tmp = []
    for i, match in enumerate(matches):
        # 组合完整的报文段
        sections.append(match[1])

    for i, section in enumerate(sections):
        # 找到body第一次出现section的位置
        start_pos = res.find(section)
        role_id = res[start_pos - 12:start_pos - 6] if start_pos >= 12 else f'role_{i}'
        role_name = bytes.fromhex(section).decode('utf-8')
        role_job = bytes.fromhex(get_role_job(section, res[start_pos:])).decode('utf-8')
        role_list_tmp.append({
            'role_id': role_id,
            'role_name_cn': role_name,
            'role_index': i,
            'old_name': section,
            'role_job': role_job
        })
    return {
        'roleSize': role_size,
        'userList': role_list_tmp
    }


def extract_packet_content(hex_data):
    """
    从协议报文中提取中文内容和服务器列表
    标准报文格式：长度(4字节) + 命令(4字节) + 内容(...字节)
    """
    try:
        # 转换为字节
        byte_data = bytes.fromhex(hex_data)

        # 分析报文头（前8字节：长度+命令）
        if len(byte_data) < 8:
            return {'error': '报文长度不足8字节'}

        # 解析长度字段（前4字节，小端序）
        content_length = struct.unpack('<I', byte_data[0:4])[0]
        # 解析命令字段（第5-8字节，小端序）
        command = struct.unpack('<I', byte_data[4:8])[0]

        # 如果报文长度小于头部8字节+内容长度，则报文不完整
        if len(byte_data) < 8 + content_length:
            actual_content = byte_data[8:]  # 使用剩余的所有字节作为内容
        else:
            actual_content = byte_data[8:8 + content_length]

        # 不再单独查找中文字符起始位置，直接解码整个内容
        # 因为协议中可能存在混合数据（非纯中文内容），我们需要保留所有有效内容
        chinese_bytes = actual_content

        # UTF-8解码
        decoded = chinese_bytes.decode('utf-8', errors='ignore')

        # 清理控制字符和无效内容
        cleaned_content = re.sub(r'[\x00-\x1f\x7f]', '', decoded)

        # 区分不同类型的报文
        is_login_response = False
        is_chat_message = False
        is_role_selection_response = False
        is_role_info_response = False

        # 首先检查是否是世界频道消息，优先处理
        is_chat_message = False
        is_login_response = False

        # 检查命令码和内容，确定报文类型
        # 登录响应：命令码0x000103E8
        if command == 0x000103E8:
            # 检查是否包含公告相关关键词
            公告关键词 = ["双旦活动公告", "一月活动", "1月活动", "新年活动", "春节活动", "元旦活动", "维护公告",
                          "群号：290172032", "通服", "空山龙吟"]
            for keyword in 公告关键词:
                if keyword in cleaned_content:
                    is_login_response = True
                    break
        # 背包物品信息：命令码0x000103D6
        elif command == 0x000103D6:
            # 这是背包物品信息报文，需要解析物品数量
            print(f"检测到d607背包物品信息报文")

            # 查找宝箱相关物品及其数量
            treasure_box_info = parse_treasure_box_info(cleaned_content)
            if treasure_box_info:
                print(f"解析到宝箱信息: {treasure_box_info}")
                # 将宝箱信息添加到返回结果中
                return {
                    'type': 'treasure_box_info',
                    'content': cleaned_content,
                    'treasure_boxes': treasure_box_info,
                    'command': command,
                    'packet_type': 'treasure_info'
                }
        # 世界频道消息：命令码0x000103F2或包含【世】标记
        elif command == 0x000103F2 or "【世】" in decoded:
            is_chat_message = True
        # 角色选择响应：命令码0x000203E8且包含角色相关关键词
        elif command == 0x000203E8 and ('姓名' in decoded or '角色' in decoded or '等级' in decoded):
            is_role_selection_response = True
        # 角色信息响应：命令码0x000108BF（0xBF080000的倒序）或包含角色属性关键词
        elif (command & 0xFFFF) == 0x08BF:  # 检查低16位是否为0x08BF
            is_role_info_response = True
        # 或者根据内容判断是否为角色信息响应
        elif '等级' in decoded and '职业' in decoded and ('物攻' in decoded or '物防' in decoded):
            is_role_info_response = True
        # 角色选择响应：命令码0x000203EE（选择角色的响应）
        elif command == 0x000203EE:
            is_role_selection_response = True
        # 检查是否是系统公告：命令是0x000103E8且包含"系统公告"标记
        elif command == 0x000103E8 and "系统公告" in cleaned_content:
            # 这是系统公告，作为聊天消息处理
            is_chat_message = True

        if is_login_response:
            # 这是登录响应报文，包含公告和服务器列表，使用原有的处理逻辑
            # 提取有效的中文文本段
            # 优先查找特定的公告标题
            priority_markers = ["双旦活动公告", "一月活动", "1月活动", "新年活动", "春节活动", "元旦活动"]
            secondary_markers = ["维护公告", "活动时间", "活动范围", "提示"]

            # 尝试查找公告标题
            title_start = -1
            # 首先查找高优先级标记
            for marker in priority_markers:
                idx = cleaned_content.find(marker)
                if idx != -1:
                    title_start = idx
                    # 查找标题结束位置（通常是换行符或冒号）
                    end_pos = idx + len(marker)
                    # 检查标题后是否有冒号或换行符
                    if end_pos < len(cleaned_content) and (
                            cleaned_content[end_pos] == ':' or cleaned_content[end_pos] == '：' or cleaned_content[
                        end_pos] == '\n'):
                        title_end = end_pos
                    else:
                        # 如果没有特殊字符，尝试查找下一个换行符
                        next_newline = cleaned_content.find('\n', end_pos)
                        if next_newline != -1:
                            title_end = next_newline
                        else:
                            title_end = end_pos
                    break

            # 如果没找到高优先级标记，再查找次级标记
            if title_start == -1:
                for marker in secondary_markers:
                    idx = cleaned_content.find(marker)
                    if idx != -1:
                        title_start = idx
                        # 查找标题结束位置
                        end_pos = idx + len(marker)
                        if end_pos < len(cleaned_content) and (
                                cleaned_content[end_pos] == ':' or cleaned_content[end_pos] == '：' or cleaned_content[
                            end_pos] == '\n'):
                            title_end = end_pos
                        else:
                            next_newline = cleaned_content.find('\n', end_pos)
                            if next_newline != -1:
                                title_end = next_newline
                            else:
                                title_end = end_pos
                        break

            # 如果找到了标题，从标题开始提取内容
            if title_start != -1:
                # 从标题开始到结尾
                valid_content = cleaned_content[title_start:]
            else:
                # 如果没有找到任何标题，返回整个内容
                valid_content = cleaned_content

            # 从公告内容中移除服务器列表和分割符部分
            # 查找群号的位置，群号后面的内容都是分割符和服务器列表
            group_num_pos = valid_content.find('群号：290172032')
            if group_num_pos != -1:
                # 只保留到群号为止的内容
                cleaned_content = valid_content[:group_num_pos + 12].strip()  # 12是"群号：290172032"的长度
            else:
                # 如果没找到群号，使用原来的方法移除服务器列表
                server_start = valid_content.find('通服')
                if server_start != -1:
                    cleaned_content = valid_content[:server_start].strip()
                else:
                    cleaned_content = valid_content

            # 优化公告格式，使其更接近真实公告
            # 将"//"替换为换行符，将"/"替换为换行符，但保留标题
            cleaned_content = cleaned_content.replace('//', '\n').replace('/', '\n')

            # 确保标题格式正确，添加"1月21日"前缀
            if cleaned_content.startswith('维护公告'):
                cleaned_content = '1月21日' + cleaned_content

            # 清理多余的空行
            cleaned_content = '\n'.join([line.strip() for line in cleaned_content.split('\n') if line.strip()])
        elif is_role_selection_response:
            # 这是角色选择响应报文，通常包含角色选择成功或其他角色相关信息
            # 这类报文通常包含姓名、角色等关键词
            # 只保留有意义的中文内容，过滤掉乱码和无意义字符
            if '姓名' in cleaned_content or '角色' in cleaned_content:
                # 尝试提取角色相关的关键信息
                # 查找所有中文字符和数字，保留有意义的内容
                chinese_parts = re.findall(r'[\u4e00-\u9fa5\w\d\s:\-\(\)]+', cleaned_content)
                if chinese_parts:
                    cleaned_content = ' '.join(chinese_parts)
                else:
                    # 如果没找到有意义的中文内容，返回原始清理后的内容
                    pass
            # 处理角色选择成功的消息
            if '角色' in cleaned_content and ('成功' in cleaned_content or '进入' in cleaned_content):
                pass  # 保留角色选择成功的信息
        elif is_role_info_response:
            # 这是角色信息响应报文，包含详细的属性信息
            # 包含等级、职业、物攻、物防、生命值、内力值等
            if '等级' in cleaned_content or '职业' in cleaned_content or '物攻' in cleaned_content:
                # 保留角色属性信息，格式化显示
                lines = cleaned_content.split('\n')
                formatted_lines = []
                for line in lines:
                    line = line.strip()
                    if line:  # 只保留非空行
                        formatted_lines.append(line)
                cleaned_content = '\n'.join(formatted_lines)
        elif is_chat_message:
            # 这是世界频道玩家喊话记录或系统公告
            # 检查是否是系统公告
            if "系统公告" in cleaned_content:
                # 只保留从"系统公告"开始的内容
                system_announcement_pos = cleaned_content.find("系统公告")
                if system_announcement_pos != -1:
                    cleaned_content = cleaned_content[system_announcement_pos:]
            else:
                # 这是普通世界频道消息
                # 跳过前面的二进制头部信息，只保留从"【世】"标记开始的实际消息内容
                world_chat_mark = "【世】"
                world_chat_pos = cleaned_content.find(world_chat_mark)
                if world_chat_pos != -1:
                    # 只保留从"【世】"开始的内容
                    cleaned_content = cleaned_content[world_chat_pos:]
            # 移除多余的空格
            cleaned_content = ' '.join(cleaned_content.split())
        else:
            # 这是游戏中的普通报文，只进行必要的清理，不进行公告格式优化
            # 移除多余的空格
            cleaned_content = ' '.join(cleaned_content.split())

            # 额外检查是否是角色信息报文（包含角色属性关键字）
            role_keywords = ['等级：', '职业：', '物攻：', '物防：', '生命值：', '内力值：', 'HP：', 'MP：', '攻击', '防御',
                             '血量', '蓝量', '命中', '躲闪', '暴击', '抗性']
            for keyword in role_keywords:
                if keyword in cleaned_content:
                    is_role_info_response = True
                    break

            # 如果是角色信息报文，格式化显示
            if is_role_info_response:
                lines = cleaned_content.split('\n')
                formatted_lines = []
                for line in lines:
                    line = line.strip()
                    if line:  # 只保留非空行
                        formatted_lines.append(line)
                cleaned_content = '\n'.join(formatted_lines)

        # 只有包含服务器列表关键词的登录响应报文才需要解析服务器列表
        server_list = []
        has_sheng_si_fu = False

        # 检查是否包含服务器列表相关关键词
        if is_login_response and ('通服' in decoded or '空山龙吟' in decoded):
            # 解析服务器列表
            server_list = parse_server_list(decoded)

        return {
            'header_info': f"报文总长度: {len(byte_data)}, 内容长度: {content_length}, 命令: 0x{command:08X}",
            'content_start_offset': 8,  # 相对于整个报文的偏移
            'content_length_field': content_length,
            'command': command,
            'cleaned_content': cleaned_content.strip(),
            'raw_decoded': decoded,
            'server_list': server_list,
            'has_sheng_si_fu': has_sheng_si_fu,
            'packet_type': 'login_response' if is_login_response else
            'chat_message' if is_chat_message else
            'role_selection' if is_role_selection_response else
            'role_info' if is_role_info_response else
            'other'
        }

    except Exception as e:
        return {'error': f'处理失败: {str(e)}'}


def get_session_id_hex(resp_hex: bytes) -> bytes:
    data = resp_hex
    idx = data.find(b'\x33\x32')
    if idx == -1:
        raise RuntimeError("没找到 3332 锚点")
    session_bytes = data[idx + 2: idx + 2 + 4]
    # 转成大端 hex 字符串（抓包顺序）
    return session_bytes.hex()


def generate_login_packet(account: str, password: str):
    oldStr = 'e80301007627baf1f5058f2700001c0000004001e0011600000'
    # 计算账号和密码的长度16进制
    acc_len_hex = hex(len(account))[2:]
    pwd_len_hex = hex(len(password))[2:]
    # ASCII转码
    acc_hex = account.encode("ASCII").hex();
    pwd_hex = password.encode("ASCII").hex();
    oldStr += acc_len_hex + '00' + acc_hex + '0' + pwd_len_hex + '00' + pwd_hex + '0000'
    bytes1 = bytes.fromhex(oldStr)
    return str(hex(len(bytes1))[2:]) + '000000' + oldStr


class Item:
    def __init__(self, name, quantity, item_id, disassemble):
        self.name = name  # 物品名称
        self.quantity = quantity  # 物品数量
        self.item_id = item_id  # 物品唯一标识
        #     是否可分解
        self.disassemble = disassemble

    def __repr__(self):
        return f"Item(name='{self.name}', quantity={self.quantity}, id='{self.item_id}', disassemble={self.disassemble})"


def find_all_positions(text, pattern):
    """查找字符串中所有指定模式的位置"""
    positions = []
    start = 0

    while True:
        pos = text.find(pattern, start)
        if pos == -1:
            break
        positions.append(pos)
        start = pos + 1

    return positions


class LoginApp:
    def __init__(self, root):
        self.root = root
        self.root.title("游戏登录界面")
        self.root.geometry("600x500")
        self.root.resizable(False, False)

        # 设置样式
        self.style = ttk.Style()
        self.style.theme_use('clam')

        self.style.configure("ReturnButton.TButton", font=("Arial", 9, "bold"), padding=4)
        # 用于存储最近处理过的报文，避免重复打印
        self.recent_packets = []  # 使用列表实现FIFO
        # 用于存储最近处理过的游戏内容
        self.recent_content = []  # 使用列表实现FIFO
        # 最大缓存数量，增加到100，减少漏解析
        self.max_cache_size = 100

        # 存储当前状态
        self.current_session_id = None
        self.current_account = None
        self.current_server = None
        self.current_server_ip = None
        self.current_server_port = None
        self.current_role = None
        self.buffer = b''  # 新增缓冲区
        # 连接状态监控
        self.connection_status = "disconnected"  # disconnected, connecting, connected, error
        self.reconnect_attempts = 0
        self.max_reconnect_attempts = 3

        # 报文完整性监控
        self.packet_statistics = {
            'total_received': 0,
            'total_processed': 0,
            'duplicates': 0,
            'errors': 0,
            'target_packets': {},  # 记录关键报文的接收情况
            'missing_packets': set()  # 记录可能遗漏的关键报文
        }

        # 配置文件路径
        self.config_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "login_config.dat")

        # 记住密码相关变量
        self.remember_password = tk.BooleanVar(value=False)
        self.saved_account = ""
        self.saved_password = ""

        # 创建主容器
        self.main_container = ttk.Frame(root)
        self.main_container.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        # 创建不同功能的框架
        # 1. 登录框架
        self.login_frame = ttk.Frame(self.main_container, padding="20")
        self.login_frame.pack(fill=tk.BOTH, expand=True)

        # 2. 公告和服务器选择框架
        self.announce_server_frame = ttk.Frame(self.main_container, padding="20")

        # 3. 角色选择框架
        self.character_frame = ttk.Frame(self.main_container, padding="20")

        # 设置主窗口背景色
        self.root.configure(bg="#f0f0f0")

        # 创建登录界面
        self._create_login_frame()
        # 创建返回登录按钮框架（专门用于放置返回按钮）
        self.bottom_button_frame = ttk.Frame(self.main_container)
        self.bottom_button_frame.pack(side=tk.BOTTOM, fill=tk.X, pady=(0, 0))

        self.return_to_login_button = ttk.Button(
            self.bottom_button_frame,
            text="← 返回登录",
            command=self._return_to_login,
            style="ReturnButton.TButton"
        )
        # 初始隐藏（仅在非登录页显示）
        self.return_to_login_button.pack(side=tk.LEFT, padx=10, pady=10)

        # 创建公告和服务器选择界面
        self._create_announce_server_frame()

        # 创建角色选择界面
        self._create_character_frame()

        # 加载保存的配置
        self._load_config()

        # 初始显示登录框架
        self._show_login_frame()
        # 新增线程锁和发送队列
        self.send_lock = threading.Lock()  # 控制发送操作的线程锁
        self.send_queue = queue.PriorityQueue()  # 存储待发送的报文
        self.send_thread = threading.Thread(target=self._send_worker, daemon=True)
        self.send_thread.start()  # 启动发送线程
        self.packet_queue = queue.Queue()  # 报文缓存队列
        self.process_thread = threading.Thread(target=self._process_packets, daemon=True)
        self.process_thread.start()

        self.lock = threading.Lock()
        self.backpack_items = {}  # 使用字典存储背包物品，key为item_id，value为Item对象

        self.saved_login_info = {
            "account": None,
            "password": None,
            "server": None,
            "role": None
        }

    def _process_packets(self):
        """处理报文队列（增强版-带完整性监控）"""
        while True:
            try:
                packet = self.packet_queue.get(timeout=1)
                self.packet_statistics['total_received'] += 1

                # 计算报文哈希用于去重
                packet_hash = hash(packet)
                packet_hex = packet.hex()
                packet_cmd = packet_hex[8:20] if len(packet_hex) >= 20 else "unknown"

                # 更新关键报文统计
                target_commands = ["e8030100d607", "e8030100ed07"]
                if packet_cmd in target_commands:
                    if packet_cmd not in self.packet_statistics['target_packets']:
                        self.packet_statistics['target_packets'][packet_cmd] = 0
                    self.packet_statistics['target_packets'][packet_cmd] += 1
                    print(
                        f"🎯 关键报文统计更新: {packet_cmd} -> {self.packet_statistics['target_packets'][packet_cmd]} 次")

                # 检查是否是重复报文
                if packet_hash in self.recent_packets:
                    self.packet_statistics['duplicates'] += 1
                    print(
                        f"🔄 检测到重复报文 #{self.packet_statistics['total_received']} (总计重复: {self.packet_statistics['duplicates']})")
                    self.packet_queue.task_done()
                    continue

                # 添加到最近处理列表
                self.recent_packets.append(packet_hash)
                if len(self.recent_packets) > self.max_cache_size:
                    self.recent_packets.pop(0)  # 移除最老的记录

                # 处理报文
                self._on_receive_packet(packet)
                self.packet_statistics['total_processed'] += 1
                self.packet_queue.task_done()

                # 定期输出处理统计
                if self.packet_statistics['total_processed'] % 100 == 0:
                    self._print_packet_statistics()

            except queue.Empty:
                # 队列为空时继续等待
                continue
            except Exception as e:
                self.packet_statistics['errors'] += 1
                print(f"❌ 处理报文时发生异常 #{self.packet_statistics['total_received']}: {str(e)}")
                self.packet_queue.task_done()

    def _print_packet_statistics(self):
        """打印报文处理统计信息"""
        stats = self.packet_statistics
        success_rate = ((stats['total_processed'] - stats['errors']) / stats['total_processed'] * 100) \
            if stats['total_processed'] > 0 else 0

        print(f"\n📊 === 报文处理统计 ===")
        print(f"总接收: {stats['total_received']} | 已处理: {stats['total_processed']} | 错误: {stats['errors']}")
        print(f"重复报文: {stats['duplicates']} | 成功率: {success_rate:.1f}%")
        print(f"缓冲区大小: {len(self.buffer)} 字节 | 队列大小: {self.packet_queue.qsize()}")

        if stats['target_packets']:
            print(f"🎯 关键报文接收统计:")
            for cmd, count in stats['target_packets'].items():
                cmd_name = {"e8030100d607": "S1礼包", "e8030100ed07": "获得物品"}.get(cmd, cmd)
                print(f"   {cmd_name} ({cmd}): {count} 次")

        # 检查是否有重要报文缺失
        expected_targets = {"e8030100d607", "e8030100ed07"}
        missing = expected_targets - set(stats['target_packets'].keys())
        if missing:
            print(f"⚠️ 可能缺失的重要报文: {missing}")
            stats['missing_packets'].update(missing)

    def _return_to_login(self):
        """统一返回登录页：清理状态 + 切换界面"""
        # 清理游戏状态（防止残留）
        if hasattr(self, 'current_sock') and self.current_sock:
            try:
                self.current_sock.close()
            except:
                pass
        if hasattr(self, 'game_socket') and self.game_socket:
            try:
                self.game_socket.close()
            except:
                pass
        # 清空队列（安全起见）
        while not self.send_queue.empty():
            try:
                self.send_queue.get_nowait()
            except:
                break
        while not self.packet_queue.empty():
            try:
                self.packet_queue.get_nowait()
            except:
                break
        # 重置关键状态
        self.current_role = None
        self.current_session_id = None
        # 切换到登录页
        self._show_login_frame()
        self.status_label.config(text="已返回登录页面", foreground="green")

    def _start_receive_thread(self, sock, callback):
        """启动接收线程（终极优化版-彻底解决报文完整性问题）"""

        def receive_loop():
            consecutive_errors = 0
            max_consecutive_errors = 20  # 进一步增加容错能力
            packet_count = 0
            error_packets = 0
            start_time = time.time()

            # 增强的监控统计
            concurrent_detection = 0
            batch_processing = 0
            fragmented_handling = 0

            # 记录关键报文类型
            target_packets = ["e8030100d607", "e8030100ed07", "e8030100ec07", "e8030100f207"]  # 增加f207世界频道
            received_targets = {}
            for target in target_packets:
                received_targets[target] = 0

            while True:
                try:
                    packet = self._receive_full_packet(sock)
                    if packet:
                        packet_count += 1
                        consecutive_errors = 0  # 重置错误计数

                        # 分析报文类型
                        packet_hex = packet.hex()
                        packet_cmd = packet_hex[8:20] if len(packet_hex) >= 20 else "unknown"

                        # 记录关键报文
                        if packet_cmd in target_packets:
                            received_targets[packet_cmd] += 1
                            target_name = {
                                "e8030100d607": "S1礼包",
                                "e8030100ed07": "获得物品",
                                "e8030100ec07": "背包信息",
                                "e8030100f207": "世界频道"
                            }.get(packet_cmd, packet_cmd)
                            print(
                                f"🎯 关键报文捕获: {target_name} ({packet_cmd}) 累计: {received_targets[packet_cmd]} 次")

                        # 检测批量处理和碎片处理情况
                        buffer_remaining = len(self.buffer)
                        if buffer_remaining > 200:  # 更敏感的批量处理检测
                            batch_processing += 1
                            print(f"🔄 批量处理检测: 缓冲区剩余{buffer_remaining}字节，连续批处理{batch_processing}次")

                        # 检测碎片化报文处理
                        if hasattr(self, '_receive_stats') and self._receive_stats.get('fragmented_packets',
                                                                                       0) > fragmented_handling:
                            fragmented_handling = self._receive_stats['fragmented_packets']
                            print(f"🔧 碎片报文处理: 累计{fragmented_handling}次大型报文重组")

                        # 检测并发报文
                        if hasattr(self, '_receive_stats') and self._receive_stats.get('concurrent_packets',
                                                                                       0) > concurrent_detection:
                            concurrent_detection = self._receive_stats['concurrent_packets']
                            print(f"⚡ 并发报文检测: 累计{concurrent_detection}次粘包处理")

                        # 更频繁的详细统计和问题检测
                        if packet_count % 15 == 0:  # 每15个报文输出一次
                            elapsed_time = time.time() - start_time
                            rate = packet_count / elapsed_time if elapsed_time > 0 else 0
                            print(f"\n📊 === 接收线程实时监控 ===")
                            print(f"总报文: {packet_count} | 错误: {error_packets} | 速率: {rate:.1f} pkt/sec")
                            print(
                                f"批处理: {batch_processing} | 并发: {concurrent_detection} | 碎片: {fragmented_handling}")
                            print(f"缓冲区: {len(self.buffer)} 字节 | 队列: {self.packet_queue.qsize()} 个")

                            # 关键报文统计
                            print(f"🎯 关键报文接收:")
                            for cmd, count in received_targets.items():
                                cmd_name = {
                                    "e8030100d607": "S1礼包",
                                    "e8030100ed07": "获得物品",
                                    "e8030100ec07": "背包信息",
                                    "e8030100f207": "世界频道"
                                }.get(cmd, cmd)
                                status = "✅" if count > 0 else "❌"
                                print(f"   {status} {cmd_name}: {count} 次")

                            # 问题检测报告
                            if error_packets > 0:
                                error_rate = (error_packets / packet_count) * 100
                                print(f"⚠️ 错误率: {error_rate:.2f}% ({error_packets}/{packet_count})")

                            # 缓冲区健康检查
                            if len(self.buffer) > 2048:
                                print(f"🚨 缓冲区警告: {len(self.buffer)} 字节数据积压")
                            elif len(self.buffer) > 0:
                                print(f"📦 缓冲区状态: {len(self.buffer)} 字节待处理")

                        # 智能队列管理
                        try:
                            # 更敏感的队列压力检测
                            if self.packet_queue.qsize() > 30:
                                print(f"⚠️ 队列压力: {self.packet_queue.qsize()} 个待处理报文")

                            self.packet_queue.put(packet, timeout=0.03)  # 更短的超时时间

                            # 队列积压严重时的紧急处理
                            if self.packet_queue.qsize() > 80:
                                print(f"🚨 队列严重积压: {self.packet_queue.qsize()} 个，考虑丢弃非关键报文")

                        except queue.Full:
                            print(f"❌ 报文队列已满({self.packet_queue.qsize()})，丢弃报文")
                            error_packets += 1
                    else:
                        # 如果_receive_full_packet返回None，说明连接可能有问题
                        consecutive_errors += 1
                        error_packets += 1
                        print(f"⚠️ 接收空报文 #{consecutive_errors}/{max_consecutive_errors}")

                        if consecutive_errors >= max_consecutive_errors:
                            print(f"❌ 连续{max_consecutive_errors}次接收失败，断开连接")
                            break
                        time.sleep(0.01)  # 更短的等待时间

                except ConnectionError as ce:
                    print(f"🔌 连接错误，停止接收: {str(ce)}")
                    break
                except Exception as e:
                    consecutive_errors += 1
                    error_packets += 1
                    print(f"❌ 接收线程异常 ({consecutive_errors}/{max_consecutive_errors}): {str(e)}")
                    if consecutive_errors >= max_consecutive_errors:
                        print(f"❌ 连续{max_consecutive_errors}次异常，断开连接")
                        break
                    time.sleep(0.02)  # 更短的异常等待时间

            # 线程结束时的详细报告
            total_time = time.time() - start_time
            success_rate = ((packet_count - error_packets) / packet_count * 100) if packet_count > 0 else 0
            print(f"\n📈 === 接收线程最终报告 ===")
            print(f"运行时间: {total_time:.1f} 秒 | 总报文: {packet_count} 个 | 成功率: {success_rate:.1f}%")
            print(f"批处理: {batch_processing} | 并发: {concurrent_detection} | 碎片: {fragmented_handling}")
            print(f"🎯 关键报文接收情况:")
            for cmd, count in received_targets.items():
                cmd_name = {
                    "e8030100d607": "S1礼包",
                    "e8030100ed07": "获得物品",
                    "e8030100ec07": "背包信息",
                    "e8030100f207": "世界频道"
                }.get(cmd, cmd)
                status = "✅" if count > 0 else "❌"
                print(f"   {status} {cmd_name}: {count} 次")

        # 启动接收线程
        thread = threading.Thread(target=receive_loop, daemon=True, name="UltimateReliableReceiver")
        thread.start()
        print(f"🚀 终极可靠型接收线程已启动: {thread.name}")

    def _receive_full_packet(self, sock):
        """接收完整报文（终极可靠版-彻底解决粘包和截断问题）"""
        try:
            # 初始化统计信息
            if not hasattr(self, '_receive_stats'):
                self._receive_stats = {
                    'total_calls': 0,
                    'successful_extractions': 0,
                    'buffer_resets': 0,
                    'concurrent_packets': 0,
                    'fragmented_packets': 0  # 新增：记录碎片化报文
                }

            self._receive_stats['total_calls'] += 1

            max_retries = 8  # 增加重试次数
            retry_count = 0

            while retry_count < max_retries:
                # 阶段1：确保有足够的数据读取报文长度（更严格的检查）
                length_wait_count = 0
                max_length_waits = 20  # 增加等待次数

                while len(self.buffer) < 4 and length_wait_count < max_length_waits:
                    try:
                        # 使用非常小的接收缓冲区来提高精度
                        chunk = sock.recv(512)  # 从1024减小到512
                        if not chunk:
                            raise ConnectionError("连接中断：无法读取报文长度")

                        with self.lock:
                            self.buffer += chunk

                        length_wait_count = 0  # 重置等待计数
                        retry_count = 0  # 重置重试计数

                        # 检查是否已经收集到足够的长度数据
                        if len(self.buffer) >= 4:
                            break

                    except socket.timeout:
                        length_wait_count += 1
                        if length_wait_count >= max_length_waits:
                            print(f"⚠️ 等待长度字段超时，缓冲区状态: {len(self.buffer)} 字节")
                            if len(self.buffer) >= 4:
                                break
                            else:
                                raise TimeoutError("等待报文长度字段超时")
                        time.sleep(0.002)  # 更短的等待时间 2ms
                        continue
                    except Exception as e:
                        length_wait_count += 1
                        if length_wait_count >= max_length_waits:
                            raise ConnectionError(f"接收长度字段失败: {str(e)}")
                        time.sleep(0.005)  # 5ms等待
                        continue

                # 阶段2：安全解析报文长度（增加更多验证）
                try:
                    with self.lock:
                        if len(self.buffer) < 4:
                            raise ValueError(f"缓冲区不足4字节: {len(self.buffer)} 字节")
                        length_bytes = self.buffer[:4]

                    length = struct.unpack('<I', length_bytes)[0]

                    # 严格验证长度（更保守的限制）
                    if length <= 0:
                        print(f"⚠️ 报文长度为零或负数: {length}，重置缓冲区")
                        with self.lock:
                            self.buffer = self.buffer[4:] if len(self.buffer) >= 4 else b''
                        self._receive_stats['buffer_resets'] += 1
                        retry_count += 1
                        continue

                    if length > 15360:  # 降低到15KB限制
                        print(f"⚠️ 报文长度过大: {length} 字节，可能为恶意数据或碎片")
                        with self.lock:
                            self.buffer = b''  # 完全清空缓冲区
                        self._receive_stats['buffer_resets'] += 1
                        retry_count += 1
                        continue

                    # 检查是否是碎片化报文的迹象
                    if length > 8192 and len(self.buffer) < length + 4:
                        print(f"🔍 检测到可能的大型报文碎片: 长度{length}，缓冲区{len(self.buffer)}字节")
                        self._receive_stats['fragmented_packets'] += 1

                except Exception as e:
                    print(f"❌ 长度解析错误: {str(e)}，缓冲区: {self.buffer.hex()[:64]}...")
                    with self.lock:
                        # 更安全的缓冲区清理
                        if len(self.buffer) >= 4:
                            self.buffer = self.buffer[4:]
                        else:
                            self.buffer = b''
                    self._receive_stats['buffer_resets'] += 1
                    retry_count += 1
                    continue

                # 阶段3：计算所需总字节数
                total_needed = 4 + length
                current_buffer_size = len(self.buffer)

                # 检测粘包情况
                if current_buffer_size > total_needed:
                    self._receive_stats['concurrent_packets'] += 1
                    print(f"🔍 检测到报文粘连: 缓冲区{current_buffer_size}字节 > 需要{total_needed}字节")
                    print(f"📊 累计粘连检测: {self._receive_stats['concurrent_packets']} 次")

                # 阶段4：确保接收完整报文内容（终极可靠版）
                content_wait_count = 0
                max_content_waits = 50  # 大幅增加等待次数
                last_buffer_size = len(self.buffer)  # 记录上次缓冲区大小
                stall_count = 0  # 增加停滞检测
                max_stall_count = 8  # 允许的最大停滞次数

                while len(self.buffer) < total_needed and content_wait_count < max_content_waits:
                    try:
                        current_buffer_size = len(self.buffer)
                        remaining_bytes = total_needed - current_buffer_size

                        # 检测接收停滞（缓冲区大小长时间不变）
                        if current_buffer_size == last_buffer_size:
                            stall_count += 1
                            if stall_count >= max_stall_count:
                                print(
                                    f"⚠️ 接收停滞检测: 缓冲区{current_buffer_size}字节停滞{stall_count}次，强制检查连接状态")
                                # 尝试发送一个小的探测包来检测连接状态
                                try:
                                    sock.send(b'')  # 发送空数据检测连接
                                except:
                                    raise ConnectionError("连接已断开")
                        else:
                            stall_count = 0  # 重置停滞计数
                            last_buffer_size = current_buffer_size

                        # 更加保守的动态接收大小调整
                        if remaining_bytes > 4096:
                            recv_size = 512  # 对大报文使用最小块
                        elif remaining_bytes > 1024:
                            recv_size = 256
                        elif remaining_bytes > 256:
                            recv_size = 128
                        else:
                            recv_size = min(64, remaining_bytes)  # 对最后少量数据使用极小块

                        print(f"📥 接收数据: 需要{remaining_bytes}字节，使用块大小{recv_size}字节")

                        chunk = sock.recv(recv_size)

                        if not chunk:
                            # 连接中断的最终检查
                            if len(self.buffer) >= total_needed:
                                print(f"✅ 连接中断但数据完整，继续处理")
                                break
                            else:
                                missing_bytes = total_needed - len(self.buffer)
                                raise ConnectionError(
                                    f"连接中断：报文不完整，缺少{missing_bytes}字节 (需要{total_needed}，当前{len(self.buffer)})")

                        with self.lock:
                            self.buffer += chunk

                        # 实时进度报告
                        new_size = len(self.buffer)
                        progress = (new_size / total_needed) * 100
                        print(
                            f"📊 接收进度: {new_size}/{total_needed} 字节 ({progress:.1f}%) 剩余{total_needed - new_size}字节")

                        content_wait_count = 0  # 重置等待计数
                        retry_count = 0  # 重置重试计数

                        # 检查是否已经收集到完整数据
                        if len(self.buffer) >= total_needed:
                            print(f"✅ 报文接收完成: {len(self.buffer)}/{total_needed} 字节")
                            break

                    except socket.timeout:
                        content_wait_count += 1
                        remaining_bytes = total_needed - len(self.buffer)
                        print(f"⏰ 接收超时 {content_wait_count}/{max_content_waits}: 仍需{remaining_bytes}字节")

                        if content_wait_count >= max_content_waits:
                            missing_bytes = total_needed - len(self.buffer)
                            raise TimeoutError(
                                f"接收报文内容超时，缺少{missing_bytes}字节 (需要{total_needed}，当前{len(self.buffer)})")

                        # 超时后使用更短的等待时间
                        time.sleep(0.002)  # 2ms等待
                        continue

                    except Exception as e:
                        content_wait_count += 1
                        if content_wait_count >= max_content_waits:
                            missing_bytes = total_needed - len(self.buffer)
                            raise ConnectionError(f"接收报文内容失败: {str(e)}，缺少{missing_bytes}字节")
                        time.sleep(0.003)  # 3ms等待
                        continue

                # 阶段5：原子性提取报文（最终验证）
                with self.lock:
                    if len(self.buffer) >= total_needed:
                        packet = self.buffer[4:4 + length]
                        # 保留剩余数据在缓冲区中（关键：处理粘包）
                        self.buffer = self.buffer[4 + length:]

                        self._receive_stats['successful_extractions'] += 1
                        success_rate = (self._receive_stats['successful_extractions'] /
                                        self._receive_stats['total_calls']) * 100

                        print(f"✅ 成功提取报文: 长度{length}字节，剩余缓冲区{len(self.buffer)}字节")
                        print(f"📈 提取成功率: {success_rate:.1f}% (总调用{self._receive_stats['total_calls']}次)")

                        # 详细的状态报告
                        if self._receive_stats['total_calls'] % 50 == 0:
                            print(f"📊 接收统计详情:")
                            print(f"   总调用: {self._receive_stats['total_calls']}")
                            print(f"   成功提取: {self._receive_stats['successful_extractions']}")
                            print(f"   缓冲区重置: {self._receive_stats['buffer_resets']}")
                            print(f"   粘包处理: {self._receive_stats['concurrent_packets']}")
                            print(f"   碎片报文: {self._receive_stats['fragmented_packets']}")

                        # 如果还有剩余数据，可能是下一个报文
                        if len(self.buffer) > 0:
                            print(f"📦 缓冲区剩余数据: {len(self.buffer)} 字节，可能包含下一报文")

                        return packet
                    else:
                        # 这就是您遇到的问题：缓冲区数据不足
                        missing_bytes = total_needed - len(self.buffer)
                        print(
                            f"⚠️ 缓冲区数据不足提取: 需要{total_needed}字节，实际{len(self.buffer)}字节，缺少{missing_bytes}字节")
                        print(f"🔍 缓冲区内容预览: {self.buffer.hex()[:64]}...")
                        retry_count += 1
                        if retry_count >= max_retries:
                            raise RuntimeError(f"多次尝试后仍无法提取完整报文，缺少{missing_bytes}字节")
                        time.sleep(0.01)  # 短暂等待后重试
                        continue

        except (ConnectionError, TimeoutError) as network_error:
            print(f"🔌 网络错误: {str(network_error)}")
            raise
        except Exception as e:
            print(f"❌ 接收报文最终失败: {str(e)}")
            with self.lock:
                self.buffer = b''
            return None

    def _recv_all(self, sock, length):
        """确保接收指定长度的数据"""
        data = b''
        while len(data) < length:
            try:
                packet = sock.recv(length - len(data))
                if not packet:
                    raise ConnectionError("连接中断")
                data += packet
            except socket.timeout:
                raise TimeoutError("接收数据超时")
            except Exception as e:
                raise RuntimeError(f"接收数据异常: {str(e)}")
        return data

    def _enqueue_packet(self, packet_hex, priority=10):
        """将报文加入发送队列，priority 越小越优先"""
        try:
            packet = binascii.unhexlify(packet_hex)
            # priority: 0=最高优先级（如使用物品、分解装备），10=普通报文
            self.send_queue.put((priority, packet))
        except Exception as e:
            print(f"加入发送队列失败: {str(e)}")

    def _send_worker(self):
        """后台发送线程，逐个处理队列中的报文"""
        while True:
            try:
                # 从队列中取出报文
                packet = self.send_queue.get(timeout=1)[1]  # 取出 (priority, packet) 中的 packet
                # 获取锁，确保单线程发送
                with self.send_lock:
                    if hasattr(self, 'current_sock') and self.current_sock:
                        self.current_sock.send(packet)
                        time.sleep(0.6)
                    else:
                        print("发送失败：未连接到游戏服务器")

                # 标记任务完成
                self.send_queue.task_done()
            except queue.Empty:
                continue  # 队列为空时继续等待
            except Exception as e:
                print(f"发送报文时发生异常: {str(e)}")

    def _save_config(self, account, password, remember=False):
        """保存登录配置"""
        try:
            # 获取当前选择的服务器
            selected_server = self.server_var.get()

            if remember:
                # 使用base64简单加密密码
                encoded_password = base64.b64encode(password.encode()).decode()
                config_data = f"{account}|{encoded_password}|{remember}|{selected_server}"
            else:
                # 不记住密码时只保存账号和服务器
                config_data = f"{account}||False|{selected_server}"

            with open(self.config_file, "w") as f:
                f.write(config_data)
            print(f"成功保存登录配置到 {self.config_file}")
        except Exception as e:
            print(f"保存登录配置失败: {str(e)}")

    def _load_config(self):
        """加载登录配置"""
        try:
            if os.path.exists(self.config_file):
                with open(self.config_file, "r") as f:
                    config_data = f.read().strip()

                if config_data:
                    parts = config_data.split("|")
                    if len(parts) >= 3:
                        saved_account = parts[0]
                        encoded_password = parts[1]
                        remember = parts[2].lower() == "true"

                        # 更新保存的账号和密码变量
                        self.saved_account = saved_account

                        # 解密密码
                        saved_password = ""
                        if remember and encoded_password:
                            try:
                                saved_password = base64.b64decode(encoded_password.encode()).decode()
                            except:
                                saved_password = ""
                        self.saved_password = saved_password

                        # 直接更新UI控件的值
                        self.account_var.set(saved_account)
                        self.password_var.set(saved_password)
                        self.remember_password.set(remember)

                        # 加载保存的服务器选择（如果有）
                        if len(parts) >= 4:
                            saved_server = parts[3]
                            # 检查保存的服务器是否在可用服务器列表中
                            if saved_server in self.server_options:
                                self.server_var.set(saved_server)
                            else:
                                # 如果保存的服务器不存在，使用默认值
                                self.server_var.set("龙一服")
                                self.remember_password.set(False)
                                self.account_var.set("")
                                self.password_var.set("")

                        print(f"成功加载登录配置: 账号={self.saved_account}, 记住密码={self.remember_password.get()}")
        except Exception as e:
            print(f"加载登录配置失败: {str(e)}")
            # 重置为默认值
            self.saved_account = ""
            self.saved_password = ""
            self.remember_password.set(False)
            self.account_var.set("")
            self.password_var.set("")

    def _create_login_frame(self):
        """创建登录框架"""
        # 清除所有现有子组件
        for widget in self.login_frame.winfo_children():
            widget.destroy()

        # 定义服务器列表
        self.server_options = {
            "龙一服": {"ip": "8.141.22.68", "port": "9988"},
            "龙二服": {"ip": "60.205.231.81", "port": "9991"}
        }

        # 游戏服务器地址映射（登录服务器和游戏服务器可能是不同的）
        self.game_server_options = {
            "龙一服": {"ip": "tlz.shuihl.cn", "port": 12065},
            "龙二服": {"ip": "tl10.shuihl.cn", "port": 12001},
            "生死符(推荐)": {"ip": "tl11.shuihl.cn", "port": 12001}
        }

        # 标题
        title_label = tk.Label(self.login_frame, text="游戏账号登录", font=('Arial', 16, 'bold'))
        title_label.pack(pady=20)

        # 账号输入
        account_frame = tk.Frame(self.login_frame)
        account_frame.pack(pady=10, padx=50, fill=tk.X)

        tk.Label(account_frame, text="账号:", width=10).pack(side=tk.LEFT)
        self.account_var = tk.StringVar(value=self.saved_account)
        self.account_entry = tk.Entry(account_frame, textvariable=self.account_var, width=30)
        self.account_entry.pack(side=tk.LEFT, expand=True, fill=tk.X)

        # 密码输入
        password_frame = tk.Frame(self.login_frame)
        password_frame.pack(pady=10, padx=50, fill=tk.X)

        tk.Label(password_frame, text="密码:", width=10).pack(side=tk.LEFT)
        self.password_var = tk.StringVar(value=self.saved_password)
        self.password_entry = tk.Entry(password_frame, textvariable=self.password_var, show="*", width=30)
        self.password_entry.pack(side=tk.LEFT, expand=True, fill=tk.X)

        # 服务器选择
        server_frame = tk.Frame(self.login_frame)
        server_frame.pack(pady=10, padx=50, fill=tk.X)

        tk.Label(server_frame, text="选择服务器:", width=10).pack(side=tk.LEFT)
        self.server_var = tk.StringVar(value="龙一服")
        self.server_combobox = ttk.Combobox(server_frame, textvariable=self.server_var,
                                            values=list(self.server_options.keys()), width=28, state="readonly")
        self.server_combobox.pack(side=tk.LEFT, expand=True, fill=tk.X)

        # 绑定服务器选择事件
        self.server_combobox.bind("<<ComboboxSelected>>", self._on_server_selected)

        # 记住密码
        remember_frame = tk.Frame(self.login_frame)
        remember_frame.pack(pady=10, padx=50, anchor=tk.W)
        self.remember_checkbox = tk.Checkbutton(remember_frame, text="记住账号密码", variable=self.remember_password)
        self.remember_checkbox.pack(side=tk.LEFT)

        # 登录按钮
        login_frame = tk.Frame(self.login_frame)
        login_frame.pack(pady=20)
        self.login_button = tk.Button(login_frame, text="登录", command=self.login, width=20, height=2)
        self.login_button.pack()

        # 进度条
        self.progress = ttk.Progressbar(self.login_frame, mode='indeterminate')
        self.progress.pack(fill=tk.X, padx=50, pady=10)
        self.progress.pack_forget()  # 初始隐藏

        # 状态标签
        self.status_label = tk.Label(self.login_frame, text="请登录", fg="red")
        self.status_label.pack(pady=10)

        # 绑定回车键到登录功能
        self.root.bind('<Return>', lambda event: self.login())

        # 设置焦点到账号输入框
        if not self.saved_account:
            self.account_entry.focus()
        elif not self.saved_password:
            self.password_entry.focus()
        else:
            self.login_button.focus()

    def _create_announce_server_frame(self):
        """创建公告和服务器选择框架"""
        # 返回登录按钮（放在最顶部，这样不会被挤走）
        self.back_to_login_btn = ttk.Button(
            self.announce_server_frame,
            text="← 返回登录",
            command=self._return_to_login,
            style="ReturnButton.TButton"
        )
        self.back_to_login_btn.pack(anchor=tk.W, pady=(0, 10))

        # 公告内容区域
        announce_frame = ttk.Frame(self.announce_server_frame, borderwidth=1, relief="solid", padding="10")
        announce_frame.pack(fill=tk.BOTH, expand=False, pady=(0, 20))
        announce_frame.config(height=150)  # 设置固定高度
        announce_frame.pack_propagate(False)  # 保持固定高度

        # 公告文本框
        self.announce_text = tk.Text(announce_frame, wrap=tk.WORD, state=tk.DISABLED, font=("Arial", 10))
        scrollbar = ttk.Scrollbar(announce_frame, orient=tk.VERTICAL, command=self.announce_text.yview)
        self.announce_text.configure(yscrollcommand=scrollbar.set)

        self.announce_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        server_frame = ttk.Frame(self.announce_server_frame, padding="10")
        server_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 20))

        # 服务器列表框
        self.server_tree = ttk.Treeview(server_frame, columns=('name', 'ip', 'port', 'status'), show='headings',
                                        height=12)
        self.server_tree.heading('name', text='服务器名称')
        self.server_tree.heading('ip', text='IP地址')
        self.server_tree.heading('port', text='端口')
        self.server_tree.heading('status', text='状态')

        self.server_tree.column('name', width=200)
        self.server_tree.column('ip', width=150)
        self.server_tree.column('port', width=80)
        self.server_tree.column('status', width=80)

        # 添加滚动条
        server_scrollbar = ttk.Scrollbar(server_frame, orient=tk.VERTICAL, command=self.server_tree.yview)
        self.server_tree.configure(yscrollcommand=server_scrollbar.set)

        self.server_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        server_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # 绑定双击事件选择服务器
        self.server_tree.bind('<Double-1>', self._on_server_double_click)

        # 状态标签
        self.server_status_label = ttk.Label(self.announce_server_frame, text="", foreground="blue")
        self.server_status_label.pack(pady=5)

    def _create_character_frame(self):
        """创建角色选择框架"""
        # 返回登录按钮（放在最顶部，这样不会被挤走）
        self.back_to_login_btn_char = ttk.Button(
            self.character_frame,
            text="← 返回登录",
            command=self._return_to_login,
            style="ReturnButton.TButton"
        )
        self.back_to_login_btn_char.pack(anchor=tk.W, pady=(0, 10))

        # 标题
        title_label = ttk.Label(self.character_frame, text="选择游戏角色", font=("Arial", 16, "bold"))
        title_label.pack(anchor=tk.W, pady=(0, 20))

        # 服务器信息
        self.server_info_label = ttk.Label(self.character_frame, text="", font=("Arial", 10))
        self.server_info_label.pack(anchor=tk.W, pady=(0, 10))

        # 角色列表区域
        self.character_list_frame = ttk.Frame(self.character_frame)
        self.character_list_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 20))

        # 按钮区域（只放进入游戏按钮）
        button_frame = ttk.Frame(self.character_frame)
        button_frame.pack(fill=tk.X, pady=10)

        self.enter_game_button = ttk.Button(button_frame, text="进入游戏", command=self._on_enter_game)
        self.enter_game_button.pack(side=tk.RIGHT, padx=5)

        # 状态标签
        self.character_status_label = ttk.Label(self.character_frame, text="", foreground="blue")
        self.character_status_label.pack(pady=5)

    def _on_server_selected(self, event=None):
        """服务器选择事件处理"""
        # 服务器选择已经通过下拉框直接处理，不需要更新额外的UI元素
        pass

    def _show_return_button(self):
        """显示返回登录按钮"""
        try:
            # 先 forget 确保重新显示
            if hasattr(self, 'bottom_button_frame'):
                self.bottom_button_frame.pack_forget()
                self.bottom_button_frame.pack(side=tk.BOTTOM, fill=tk.X, pady=(0, 0))
            if hasattr(self, 'return_to_login_button'):
                self.return_to_login_button.pack_forget()
                self.return_to_login_button.pack(side=tk.LEFT, padx=10, pady=10)
            print("返回按钮已显示")
        except Exception as e:
            print(f"显示返回按钮失败: {str(e)}")

    def _show_login_frame(self):
        """显示登录框架"""
        self.announce_server_frame.pack_forget()
        self.character_frame.pack_forget()
        self.login_frame.pack(fill=tk.BOTH, expand=True)
        self.bottom_button_frame.pack_forget()  # 隐藏返回按钮
        # 重置登录按钮状态
        self.login_button.config(state='normal')
        self.status_label.config(text="", foreground="red")

    def _show_announce_server_frame(self):
        """显示公告和服务器选择框架"""
        # 先隐藏所有框架
        self.login_frame.pack_forget()
        self.character_frame.pack_forget()
        # 隐藏游戏主界面（如果已创建）
        if hasattr(self, 'game_main_frame') and self.game_main_frame:
            self.game_main_frame.pack_forget()

        # 显示页面内容（返回按钮已在页面创建时添加）
        self.announce_server_frame.pack(fill=tk.BOTH, expand=True)

    def _show_character_frame(self):
        """显示角色选择框架"""
        self.login_frame.pack_forget()
        self.announce_server_frame.pack_forget()
        # 隐藏游戏主界面（如果已创建）
        if hasattr(self, 'game_main_frame') and self.game_main_frame:
            self.game_main_frame.pack_forget()
        self.character_frame.pack(fill=tk.BOTH, expand=True)
        # 角色选择页面不需要返回按钮

    def login(self):
        """执行登录操作"""
        account = self.account_var.get().strip()
        password = self.password_var.get().strip()
        server_name = self.server_var.get().strip()

        # 输入验证
        if not account:
            self.status_label.config(text="请输入账号", foreground="red")
            return
        if not password:
            self.status_label.config(text="请输入密码", foreground="red")
            return
        if not server_name:
            self.status_label.config(text="请选择服务器", foreground="red")
            return

        # 从服务器选项中获取IP和端口
        server_config = self.server_options.get(server_name)
        if not server_config:
            self.status_label.config(text="无效的服务器选择", foreground="red")
            return

        server = server_config["ip"]
        port = int(server_config["port"])

        # 显示进度条并禁用登录按钮
        self.progress.pack(fill=tk.X, padx=50, pady=10)
        self.progress.start()
        self.login_button.config(state='disabled')
        self.status_label.config(text="正在登录...")

        # 在新线程中执行登录操作，防止界面冻结
        thread = threading.Thread(target=self._perform_login, args=(account, password, server, port))
        thread.daemon = True
        thread.start()

    def _perform_login(self, account, password, server, port):
        """在后台线程中执行实际的登录操作"""
        print(f"_perform_login被调用，参数: account={account}, password={password}, server={server}, port={port}")

        try:
            # 生成登录包
            print(f"生成登录包...")
            body = generate_login_packet(account, password)
            print(f"登录包生成成功，长度: {len(body)} 字符")
            print(f"登录包内容: {body}")

            packet = binascii.unhexlify(body)
            print(f"转换为字节成功，长度: {len(packet)} 字节")

            # 连接到服务器
            print(f"创建套接字...")
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            print(f"套接字创建成功: {sock}")

            sock.settimeout(10)  # 10秒超时
            print(f"设置套接字超时为10秒")

            print(f"正在连接到服务器: {server}:{port}...")
            sock.connect((server, port))
            print(f"成功连接到服务器: {server}:{port}")

            # 发送登录包
            print(f"正在发送登录包，长度: {len(packet)} 字节...")
            sent = sock.send(packet)
            print(f"成功发送 {sent} 字节")

            # 接收响应
            print(f"正在等待服务器响应...")
            response = sock.recv(1024)
            print(f"成功接收响应，长度: {len(response)} 字节")
            print(f"响应内容: {response.hex()}")

            # 关闭登录服务器连接，因为游戏服务器需要单独连接
            sock.close()
            print(f"成功关闭登录服务器连接")

            if response:
                # 解析响应
                print(f"响应长度: {len(response)} 字节")
                if len(response) >= 8:
                    # 获取session ID
                    print(f"响应长度足够，尝试提取session ID...")
                    try:
                        session_id = get_session_id_hex(response)
                        print(f"成功提取session ID: {session_id}")

                        # 登录成功，但角色数据需要在连接游戏服务器后获取
                        # 这里只需要session ID和服务器列表

                        # 在主线程中更新UI
                        print(f"调用_on_login_success...")
                        self.root.after(0, self._on_login_success, account, session_id, response)
                        return
                    except Exception as e:
                        error_msg = f"提取session ID失败: {str(e)}"
                        print(f"{error_msg}")
                        self.root.after(0, self._on_login_failed, error_msg)
                        return

            error_msg = "服务器没有返回有效响应"
            print(f"{error_msg}")
            self.root.after(0, self._on_login_failed, error_msg)

        except socket.timeout as timeout_error:
            error_msg = f"连接服务器超时: {str(timeout_error)}"
            print(f"{error_msg}")
            self.root.after(0, self._on_login_failed, error_msg)
        except ConnectionRefusedError as conn_error:
            error_msg = f"无法连接到服务器: {str(conn_error)}"
            print(f"{error_msg}")
            self.root.after(0, self._on_login_failed, error_msg)
        except Exception as e:
            error_msg = f"登录过程中发生错误: {str(e)}"
            print(f"{error_msg}")
            import traceback
            traceback.print_exc()
            self.root.after(0, self._on_login_failed, error_msg)

    def extract_packet_content(hex_data):
        """
        从协议报文中提取中文内容和服务器列表
        标准报文格式：长度(4字节) + 命令(4字节) + 内容(...字节)
        """
        try:
            # 转换为字节
            byte_data = bytes.fromhex(hex_data)

            # 分析报文头（前8字节：长度+命令）
            if len(byte_data) < 8:
                return {'error': '报文长度不足8字节'}

            # 解析长度字段（前4字节，小端序）
            content_length = struct.unpack('<I', byte_data[0:4])[0]
            # 解析命令字段（第5-8字节，小端序）
            command = struct.unpack('<I', byte_data[4:8])[0]

            # 如果报文长度小于头部8字节+内容长度，则报文不完整
            if len(byte_data) < 8 + content_length:
                actual_content = byte_data[8:]  # 使用剩余的所有字节作为内容
            else:
                actual_content = byte_data[8:8 + content_length]

            # 不再单独查找中文字符起始位置，直接解码整个内容
            # 因为协议中可能存在混合数据（非纯中文内容），我们需要保留所有有效内容
            chinese_bytes = actual_content

            # UTF-8解码
            decoded = chinese_bytes.decode('utf-8', errors='ignore')

            # 清理控制字符和无效内容
            cleaned_content = re.sub(r'[\x00-\x1f\x7f]', '', decoded)

            # 区分不同类型的报文
            is_login_response = False
            is_chat_message = False
            is_role_selection_response = False
            is_role_info_response = False

            # 首先检查是否是世界频道消息，优先处理
            is_chat_message = False
            is_login_response = False

            # 检查命令码和内容，确定报文类型
            # 登录响应：命令码0x000103E8
            if command == 0x000103E8:
                # 检查是否包含公告相关关键词
                公告关键词 = ["双旦活动公告", "一月活动", "1月活动", "新年活动", "春节活动", "元旦活动", "维护公告",
                              "群号：290172032", "通服", "空山龙吟"]
                for keyword in 公告关键词:
                    if keyword in cleaned_content:
                        is_login_response = True
                        break
            # 背包物品信息：命令码0x000103D6
            elif command == 0x000103D6:
                # 这是背包物品信息报文，需要解析物品数量
                print(f"检测到d607背包物品信息报文")

                # 查找宝箱相关物品及其数量
                treasure_box_info = parse_treasure_box_info(cleaned_content)
                if treasure_box_info:
                    print(f"解析到宝箱信息: {treasure_box_info}")
                    # 将宝箱信息添加到返回结果中
                    return {
                        'type': 'treasure_box_info',
                        'content': cleaned_content,
                        'treasure_boxes': treasure_box_info,
                        'command': command,
                        'packet_type': 'treasure_info'
                    }
            # 世界频道消息：命令码0x000103F2或包含【世】标记
            elif command == 0x000103F2 or "【世】" in decoded:
                is_chat_message = True
            # 角色选择响应：命令码0x000203E8且包含角色相关关键词
            elif command == 0x000203E8 and ('姓名' in decoded or '角色' in decoded or '等级' in decoded):
                is_role_selection_response = True
            # 角色信息响应：命令码0x000108BF（0xBF080000的倒序）或包含角色属性关键词
            elif (command & 0xFFFF) == 0x08BF:  # 检查低16位是否为0x08BF
                is_role_info_response = True
            # 或者根据内容判断是否为角色信息响应
            elif '等级' in decoded and '职业' in decoded and ('物攻' in decoded or '物防' in decoded):
                is_role_info_response = True
            # 角色选择响应：命令码0x000203EE（选择角色的响应）
            elif command == 0x000203EE:
                is_role_selection_response = True
            # 检查是否是系统公告：命令是0x000103E8且包含"系统公告"标记
            elif command == 0x000103E8 and "系统公告" in cleaned_content:
                # 这是系统公告，作为聊天消息处理
                is_chat_message = True

            if is_login_response:
                # 这是登录响应报文，包含公告和服务器列表，使用原有的处理逻辑
                # 提取有效的中文文本段
                # 优先查找特定的公告标题
                priority_markers = ["双旦活动公告", "一月活动", "1月活动", "新年活动", "春节活动", "元旦活动"]
                secondary_markers = ["维护公告", "活动时间", "活动范围", "提示"]

                # 尝试查找公告标题
                title_start = -1
                title_end = -1

                # 首先查找高优先级标记
                for marker in priority_markers:
                    idx = cleaned_content.find(marker)
                    if idx != -1:
                        title_start = idx
                        # 查找标题结束位置（通常是换行符或冒号）
                        end_pos = idx + len(marker)
                        # 检查标题后是否有冒号或换行符
                        if end_pos < len(cleaned_content) and (
                                cleaned_content[end_pos] == ':' or cleaned_content[end_pos] == '：' or cleaned_content[
                            end_pos] == '\n'):
                            title_end = end_pos
                        else:
                            # 如果没有特殊字符，尝试查找下一个换行符
                            next_newline = cleaned_content.find('\n', end_pos)
                            if next_newline != -1:
                                title_end = next_newline
                            else:
                                title_end = end_pos
                        break

                # 如果没找到高优先级标记，再查找次级标记
                if title_start == -1:
                    for marker in secondary_markers:
                        idx = cleaned_content.find(marker)
                        if idx != -1:
                            title_start = idx
                            # 查找标题结束位置
                            end_pos = idx + len(marker)
                            if end_pos < len(cleaned_content) and (
                                    cleaned_content[end_pos] == ':' or cleaned_content[end_pos] == '：' or
                                    cleaned_content[
                                        end_pos] == '\n'):
                                title_end = end_pos
                            else:
                                next_newline = cleaned_content.find('\n', end_pos)
                                if next_newline != -1:
                                    title_end = next_newline
                                else:
                                    title_end = end_pos
                            break

                # 如果找到了标题，从标题开始提取内容
                if title_start != -1:
                    # 从标题开始到结尾
                    valid_content = cleaned_content[title_start:]
                else:
                    # 如果没有找到任何标题，返回整个内容
                    valid_content = cleaned_content

                # 从公告内容中移除服务器列表和分割符部分
                # 查找群号的位置，群号后面的内容都是分割符和服务器列表
                group_num_pos = valid_content.find('群号：290172032')
                if group_num_pos != -1:
                    # 只保留到群号为止的内容
                    cleaned_content = valid_content[:group_num_pos + 12].strip()  # 12是"群号：290172032"的长度
                else:
                    # 如果没找到群号，使用原来的方法移除服务器列表
                    server_start = valid_content.find('通服')
                    if server_start != -1:
                        cleaned_content = valid_content[:server_start].strip()
                    else:
                        cleaned_content = valid_content

                # 优化公告格式，使其更接近真实公告
                # 将"//"替换为换行符，将"/"替换为换行符，但保留标题
                cleaned_content = cleaned_content.replace('//', '\n').replace('/', '\n')

                # 确保标题格式正确，添加"1月21日"前缀
                if cleaned_content.startswith('维护公告'):
                    cleaned_content = '1月21日' + cleaned_content

                # 清理多余的空行
                cleaned_content = '\n'.join([line.strip() for line in cleaned_content.split('\n') if line.strip()])
            elif is_role_selection_response:
                # 这是角色选择响应报文，通常包含角色选择成功或其他角色相关信息
                # 这类报文通常包含姓名、角色等关键词
                # 只保留有意义的中文内容，过滤掉乱码和无意义字符
                if '姓名' in cleaned_content or '角色' in cleaned_content:
                    # 尝试提取角色相关的关键信息
                    # 查找所有中文字符和数字，保留有意义的内容
                    chinese_parts = re.findall(r'[\u4e00-\u9fa5\w\d\s:\-\(\)]+', cleaned_content)
                    if chinese_parts:
                        cleaned_content = ' '.join(chinese_parts)
                    else:
                        # 如果没找到有意义的中文内容，返回原始清理后的内容
                        pass
                # 处理角色选择成功的消息
                if '角色' in cleaned_content and ('成功' in cleaned_content or '进入' in cleaned_content):
                    pass  # 保留角色选择成功的信息
            elif is_role_info_response:
                # 这是角色信息响应报文，包含详细的属性信息
                # 包含等级、职业、物攻、物防、生命值、内力值等
                if '等级' in cleaned_content or '职业' in cleaned_content or '物攻' in cleaned_content:
                    # 保留角色属性信息，格式化显示
                    lines = cleaned_content.split('\n')
                    formatted_lines = []
                    for line in lines:
                        line = line.strip()
                        if line:  # 只保留非空行
                            formatted_lines.append(line)
                    cleaned_content = '\n'.join(formatted_lines)
            elif is_chat_message:
                # 这是世界频道玩家喊话记录或系统公告
                # 检查是否是系统公告
                if "系统公告" in cleaned_content:
                    # 只保留从"系统公告"开始的内容
                    system_announcement_pos = cleaned_content.find("系统公告")
                    if system_announcement_pos != -1:
                        cleaned_content = cleaned_content[system_announcement_pos:]
                else:
                    # 这是普通世界频道消息
                    # 跳过前面的二进制头部信息，只保留从"【世】"标记开始的实际消息内容
                    world_chat_mark = "【世】"
                    world_chat_pos = cleaned_content.find(world_chat_mark)
                    if world_chat_pos != -1:
                        # 只保留从"【世】"开始的内容
                        cleaned_content = cleaned_content[world_chat_pos:]
                # 移除多余的空格
                cleaned_content = ' '.join(cleaned_content.split())
            else:
                # 这是游戏中的普通报文，只进行必要的清理，不进行公告格式优化
                # 移除多余的空格
                cleaned_content = ' '.join(cleaned_content.split())

                # 额外检查是否是角色信息报文（包含角色属性关键字）
                role_keywords = ['等级：', '职业：', '物攻：', '物防：', '生命值：', '内力值：', 'HP：', 'MP：', '攻击', '防御',
                                 '血量', '蓝量', '命中', '躲闪', '暴击', '抗性']
                for keyword in role_keywords:
                    if keyword in cleaned_content:
                        is_role_info_response = True
                        break

                # 如果是角色信息报文，格式化显示
                if is_role_info_response:
                    lines = cleaned_content.split('\n')
                    formatted_lines = []
                    for line in lines:
                        line = line.strip()
                        if line:  # 只保留非空行
                            formatted_lines.append(line)
                    cleaned_content = '\n'.join(formatted_lines)

            # 只有包含服务器列表关键词的登录响应报文才需要解析服务器列表
            server_list = []
            has_sheng_si_fu = False

            # 检查是否包含服务器列表相关关键词
            if is_login_response and ('通服' in decoded or '空山龙吟' in decoded):
                # 解析服务器列表
                server_list = parse_server_list(decoded)

            return {
                'header_info': f"报文总长度: {len(byte_data)}, 内容长度: {content_length}, 命令: 0x{command:08X}",
                'content_start_offset': 8,  # 相对于整个报文的偏移
                'content_length_field': content_length,
                'command': command,
                'cleaned_content': cleaned_content.strip(),
                'raw_decoded': decoded,
                'server_list': server_list,
                'has_sheng_si_fu': has_sheng_si_fu,
                'packet_type': 'login_response' if is_login_response else
                'chat_message' if is_chat_message else
                'role_selection' if is_role_selection_response else
                'role_info' if is_role_info_response else
                'other'
            }

        except Exception as e:
            return {'error': f'处理失败: {str(e)}'}

    def _on_login_success(self, account, session_id, response):
        # 保存登录信息
        self.saved_login_info["account"] = account
        self.saved_login_info["password"] = self.password_var.get().strip()
        self.saved_login_info["server"] = self.server_var.get()
        self.saved_login_info["role"] = self.current_role
        """登录成功回调"""
        self.progress.stop()
        self.progress.pack_forget()
        self.login_button.config(state='normal')
        self.status_label.config(text=f"登录成功! 欢迎 {account}", foreground="green")

        # 保存当前状态
        self.current_session_id = session_id
        self.current_account = account

        # 保存登录配置（如果用户选择了记住密码）
        password = self.password_var.get().strip()
        remember = self.remember_password.get()
        self._save_config(account, password, remember)

        # 更新公告和服务器列表
        self._update_announcement_and_servers(response)

        # 显示公告和服务器选择框架
        self._show_announce_server_frame()

        # 检查是否是自动重连模式
        if hasattr(self, '_is_auto_reconnect') and self._is_auto_reconnect:
            print("自动重连模式：正在自动选择服务器...")
            self._is_auto_reconnect = False  # 重置标志
            # 延迟一下让服务器列表渲染完成
            self.root.after(500, self._auto_select_server)

    def _update_announcement_and_servers(self, response):
        """更新公告和服务器列表"""
        try:
            chinese_content = extract_packet_content(response.hex())

            # 添加调试日志
            print("调试信息: extract_packet_content返回结果")
            print(f"  cleaned_content: {chinese_content.get('cleaned_content', '无')}")
            print(f"  服务器列表长度: {len(chinese_content.get('server_list', []))}")

            # 更新公告内容
            self.announce_text.config(state=tk.NORMAL)
            self.announce_text.delete(1.0, tk.END)

            if 'cleaned_content' in chinese_content and chinese_content['cleaned_content']:
                self.announce_text.insert(tk.END, chinese_content['cleaned_content'])
            else:
                self.announce_text.insert(tk.END, "暂无公告内容")

            self.announce_text.config(state=tk.DISABLED)

            # 清除现有服务器列表
            for item in self.server_tree.get_children():
                self.server_tree.delete(item)

            # 更新服务器列表
            # 从服务器响应中动态提取，不写死
            servers = []
            if 'server_list' in chinese_content and chinese_content['server_list']:
                servers = chinese_content['server_list']
                print(f"调试信息: 从服务器响应中提取到 {len(servers)} 个服务器")
            else:
                # 如果没有提取到服务器列表，显示提示信息
                print("调试信息: 从服务器响应中未提取到服务器列表")

            # 添加服务器数据
            for server in servers:
                status_text = "在线" if server["status"] == "online" else "离线"
                self.server_tree.insert('', tk.END, values=(server["name"], server["ip"], server["port"], status_text))

        except Exception as e:
            # 更新公告内容为错误信息
            self.announce_text.config(state=tk.NORMAL)
            self.announce_text.delete(1.0, tk.END)
            self.announce_text.insert(tk.END, f"获取公告失败: {str(e)}")
            self.announce_text.config(state=tk.DISABLED)
            print(f"调试信息: 获取公告失败 - {str(e)}")

    def _on_server_double_click(self, event):
        """双击选择服务器"""
        self._on_select_server()

    def _on_select_server(self):
        """选择服务器按钮点击事件"""
        selected_items = self.server_tree.selection()
        if selected_items:
            selected_item = selected_items[0]
            server_info = self.server_tree.item(selected_item)['values']
            server_name, server_ip, server_port, status = server_info

            # 保存当前服务器信息
            self.current_server = server_name
            self.current_server_ip = server_ip
            self.current_server_port = int(server_port)

            # 更新状态
            self.server_status_label.config(text=f"正在获取角色列表...", foreground="orange")

            # 在后台线程中获取角色列表
            def get_roles():
                try:
                    # 游戏服务器需要新的socket连接，不使用登录服务器的socket
                    # 因为登录服务器和游戏服务器是不同的服务器
                    role_list, sock = connect_game_server_get_roles(
                        self.current_session_id,
                        server_ip,
                        int(server_port),
                        None  # 明确不传递socket对象，让函数创建新的socket连接游戏服务器
                    )

                    # 保存socket对象，以便后续进入游戏时使用
                    self.current_sock = sock
                    # 在主线程中更新UI，只传递角色列表
                    self.root.after(0, self._update_character_list, role_list)
                except Exception as e:
                    self.root.after(0, self._show_character_error, f"获取角色列表时发生错误: {str(e)}")

            # 启动后台线程
            thread = threading.Thread(target=get_roles)
            thread.daemon = True
            thread.start()
        else:
            self.server_status_label.config(text="请先选择一个服务器", foreground="red")

    def _update_character_list(self, role_list):
        """更新角色列表"""
        # 清除现有角色按钮
        for widget in self.character_list_frame.winfo_children():
            widget.destroy()

        # 更新服务器信息
        self.server_info_label.config(
            text=f"当前服务器: {self.current_server} ({self.current_server_ip}:{self.current_server_port})")

        # 更新状态
        self.server_status_label.config(text="", foreground="blue")

        # 检查是否有角色列表
        if role_list and 'userList' in role_list and len(role_list['userList']) > 0:
            # 保存角色列表
            self.current_role_list = role_list['userList']

            # 添加角色按钮
            for role in role_list['userList']:
                btn = ttk.Button(
                    self.character_list_frame,
                    text=f"{role['role_name_cn']} (ID: {role['role_id']})",
                    command=lambda r=role: self._select_character(r)
                )
                btn.pack(pady=5, fill=tk.X)
                # 绑定双击事件：直接进入游戏
                btn.bind('<Double-1>', lambda event, r=role: self._on_double_click_enter_game(r))

            # 显示角色选择框架
            self._show_character_frame()

            # 检查是否是自动重连模式
            if hasattr(self, '_is_auto_reconnect') and self._is_auto_reconnect:
                print("自动重连模式：正在自动选择角色...")
                self.root.after(500, self._auto_select_character)
        elif 'error' in role_list:
            error_msg = role_list['error']
            print(f"调试信息: 处理角色列表错误: {error_msg}")

            # 检查是否为登录异常
            if '登录异常' in error_msg or '请重新登录' in error_msg:
                # 显示登录异常，要求重新登录
                self._show_character_error(f"登录异常: {error_msg}，请重新登录")
                # 可以选择自动返回到登录界面
                # self.root.after(2000, self._show_login_frame)
            else:
                self._show_character_error(f"获取角色失败: {error_msg}")
        else:
            self._show_character_error("没有找到可用角色")

    def _show_modern_dialog(self, title, message, dialog_type="info", buttons=["确定"]):
        """显示现代化模态对话框

        Args:
            title (str): 对话框标题
            message (str): 对话框内容
            dialog_type (str): 类型 - info/warning/error/success
            buttons (list): 按钮列表
        """
        # 创建顶层窗口
        dialog = tk.Toplevel(self.root)
        dialog.title(title)
        dialog.geometry("400x200")
        dialog.resizable(False, False)
        dialog.transient(self.root)  # 设置为模态对话框
        dialog.grab_set()  # 捕获所有事件

        # 居中显示
        dialog.update_idletasks()
        x = (dialog.winfo_screenwidth() // 2) - (400 // 2)
        y = (dialog.winfo_screenheight() // 2) - (200 // 2)
        dialog.geometry(f"400x200+{x}+{y}")

        # 设置窗口样式
        dialog.configure(bg="#f8f9fa")

        # 创建主框架
        main_frame = tk.Frame(dialog, bg="#f8f9fa", padx=20, pady=20)
        main_frame.pack(fill=tk.BOTH, expand=True)

        # 标题框架
        title_frame = tk.Frame(main_frame, bg="#f8f9fa")
        title_frame.pack(fill=tk.X, pady=(0, 15))

        # 图标和标题
        icon_map = {
            "info": "🔵",
            "warning": "⚠️",
            "error": "❌",
            "success": "✅"
        }

        icon_label = tk.Label(
            title_frame,
            text=icon_map.get(dialog_type, "ℹ️"),
            font=("Arial", 16),
            bg="#f8f9fa"
        )
        icon_label.pack(side=tk.LEFT, padx=(0, 10))

        title_label = tk.Label(
            title_frame,
            text=title,
            font=("Arial", 12, "bold"),
            bg="#f8f9fa",
            fg="#333333"
        )
        title_label.pack(side=tk.LEFT)

        # 分隔线
        separator = tk.Frame(main_frame, height=1, bg="#dee2e6")
        separator.pack(fill=tk.X, pady=(0, 15))

        # 内容区域
        content_frame = tk.Frame(main_frame, bg="#f8f9fa")
        content_frame.pack(fill=tk.BOTH, expand=True)

        message_label = tk.Label(
            content_frame,
            text=message,
            font=("Arial", 10),
            bg="#f8f9fa",
            fg="#495057",
            wraplength=350,
            justify=tk.LEFT
        )
        message_label.pack(anchor=tk.W)

        # 按钮框架
        button_frame = tk.Frame(main_frame, bg="#f8f9fa")
        button_frame.pack(fill=tk.X, pady=(15, 0))

        # 存储按钮结果
        dialog.result = None

        def on_button_click(result_value):
            dialog.result = result_value
            dialog.destroy()

        # 创建按钮
        for i, button_text in enumerate(buttons):
            btn_style = {
                "info": {"bg": "#17a2b8", "fg": "white", "active_bg": "#138496"},
                "warning": {"bg": "#ffc107", "fg": "black", "active_bg": "#e0a800"},
                "error": {"bg": "#dc3545", "fg": "white", "active_bg": "#c82333"},
                "success": {"bg": "#28a745", "fg": "white", "active_bg": "#218838"}
            }.get(dialog_type, {"bg": "#6c757d", "fg": "white", "active_bg": "#5a6268"})

            button = tk.Button(
                button_frame,
                text=button_text,
                font=("Arial", 9, "bold"),
                bg=btn_style["bg"],
                fg=btn_style["fg"],
                activebackground=btn_style["active_bg"],
                activeforeground=btn_style["fg"],
                relief=tk.FLAT,
                bd=0,
                padx=20,
                pady=8,
                cursor="hand2",
                command=lambda val=button_text: on_button_click(val)
            )
            button.pack(side=tk.RIGHT if i == 0 else tk.LEFT, padx=5)

        # ESC键关闭对话框
        dialog.bind('<Escape>', lambda e: on_button_click(None))

        # 等待对话框关闭
        dialog.wait_window()
        return dialog.result

    # 便捷弹窗方法
    def show_info(self, title, message):
        """显示信息对话框"""
        return self._show_modern_dialog(title, message, "info", ["确定"])

    def show_warning(self, title, message, buttons=["确定"]):
        """显示警告对话框"""
        return self._show_modern_dialog(title, message, "warning", buttons)

    def show_error(self, title, message, buttons=["确定"]):
        """显示错误对话框"""
        return self._show_modern_dialog(title, message, "error", buttons)

    def show_success(self, title, message, buttons=["确定"]):
        """显示成功对话框"""
        return self._show_modern_dialog(title, message, "success", buttons)

    def show_confirm(self, title, message):
        """显示确认对话框"""
        return self._show_modern_dialog(title, message, "warning", ["确定", "取消"])

    def _on_double_click_enter_game(self, role):
        """双击角色直接进入游戏"""
        print(f"双击角色: {role['role_name_cn']}, 准备直接进入游戏...")
        # 直接选择角色并进入游戏
        self._select_character(role)
        # 然后触发进入游戏
        self._on_enter_game()

    def _select_character(self, role):
        """选择角色后执行操作"""
        # 🔴 强校验：确保 role 是有效字典且包含必要字段
        if not isinstance(role, dict) or not role.get('role_name_cn') or not role.get('role_id'):
            print(f"❌ _select_character 接收到无效 role: {role}")
            self.character_status_label.config(text="角色数据异常，请刷新重试", foreground="red")
            return

        # ✅ 安全赋值
        self.current_role = role.copy()  # 防止外部修改影响
        print(f"✅ 已选择角色: ID={self.current_role['role_id']}, 名称={self.current_role['role_name_cn']}")

        # 更新状态
        self.character_status_label.config(
            text="已选择角色，点击'进入游戏'开始游戏",
            foreground="green"
        )

        # 🔵 显式启用按钮（防止因其他逻辑误禁用）
        self.enter_game_button.config(state='normal')

    def _show_connection_error(self, error_msg):
        """显示连接错误（使用现代化对话框）"""
        self.character_status_label.config(text=f"连接失败: {error_msg}", foreground="red")

        # 显示错误对话框
        result = self._show_modern_dialog(
            title="连接失败",
            message=f"无法连接到游戏服务器：{error_msg}\n\n是否要重新连接？",
            dialog_type="warning",
            buttons=["重新连接", "返回首页"]
        )

        # 根据用户选择执行相应操作
        if result == "重新连接":
            # 可以在这里添加重新连接逻辑
            pass
        elif result == "返回首页":
            self._show_login_frame()

    def _on_enter_game(self):
        """进入游戏按钮点击事件"""
        # 记录日志
        print("点击了'进入游戏'按钮")

        if self.current_role:
            # 更新状态
            self.character_status_label.config(text="正在进入游戏...", foreground="orange")

            # 禁用进入游戏按钮，防止重复点击
            self.enter_game_button.config(state='disabled')

            # 直接显示游戏主界面，网络连接放在后台线程中执行
            print(
                f"准备进入游戏，角色: {self.current_role['role_name_cn']}, 服务器: {self.current_server_ip}:{self.current_server_port}")

            # 先显示游戏主界面，避免UI卡顿
            try:
                # 隐藏所有现有框架
                self.login_frame.pack_forget()
                self.announce_server_frame.pack_forget()
                self.character_frame.pack_forget()

                # 创建游戏主界面框架
                self.game_main_frame = ttk.Frame(self.main_container, padding="20")
                self.game_main_frame.pack(fill=tk.BOTH, expand=True)

                # 修改窗口标题
                self.root.title(f"游戏主界面 - {self.current_role['role_name_cn']}-{self.current_role['role_job']}")

                # 创建全局工具栏（在顶部，notebook上方）
                self._create_global_toolbar()

                # 创建标签页控件框架（在左侧或中间）
                content_frame = ttk.Frame(self.game_main_frame)
                content_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True, pady=(5, 0))

                self.notebook = ttk.Notebook(content_frame)
                self.notebook.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

                # 创建聊天页面（综合聊天）
                chat_frame = ttk.Frame(self.notebook)
                self.notebook.add(chat_frame, text="综合聊天")
                self.create_chat_page(chat_frame)

                # 创建世界聊天页面
                world_chat_frame = ttk.Frame(self.notebook)
                self.notebook.add(world_chat_frame, text="世界聊天")
                self.create_world_chat_page(world_chat_frame)

                # 创建报文记录页面
                packet_log_frame = ttk.Frame(self.notebook)
                self.notebook.add(packet_log_frame, text="报文记录")
                self.create_packet_log_page(packet_log_frame)

                # 创建背包页面
                backpack_frame = ttk.Frame(self.notebook)
                self.notebook.add(backpack_frame, text="背包")
                self.create_backpack_page(backpack_frame)

                # 创建仓库页面
                warehouse_frame = ttk.Frame(self.notebook)
                self.notebook.add(warehouse_frame, text="仓库")
                self.create_warehouse_page(warehouse_frame)

                # 创建角色信息页面
                role_info_frame = ttk.Frame(self.notebook)
                self.notebook.add(role_info_frame, text="角色信息")
                self.create_role_info_page(role_info_frame)

                # 记录日志
                print("成功显示游戏主界面")

                # 游戏主界面有自己的工具栏，隐藏底部返回按钮
                if hasattr(self, 'bottom_button_frame'):
                    self.bottom_button_frame.pack_forget()

                # 将网络连接操作放在后台线程中执行，避免阻塞UI
                def connect_server_thread():
                    try:
                        # 使用已保存的socket对象发送选择角色请求
                        if hasattr(self, 'current_sock') and self.current_sock:
                            print(f"使用已保存的socket对象发送选择角色请求...")

                            # 设置套接字超时
                            self.current_sock.settimeout(5)  # 设置5秒超时
                            print(f"成功设置套接字超时为5秒")

                            # 使用用户提供的正确选择角色请求格式  18000000e8030200ee0397f6f505f1030000060000006a5302000000

                            body = '18000000e8030200ee0397f6f505f103000006000000485302000000'
                            print(f"原始选择角色请求: {body}")

                            # 替换角色ID
                            body = body.replace("485302", self.current_role['role_id'])
                            print(f"替换角色ID后的请求: {body}")

                            packet = binascii.unhexlify(body)
                            print(f"转换为字节后的请求: {packet.hex()}")

                            sent = self.current_sock.send(packet)
                            print(f"成功发送选择角色请求，发送字节数: {sent}")

                            # 接收角色选择响应
                            print("接收角色选择响应...")
                            response = self.current_sock.recv(4048)
                            print(f"成功接收到角色选择响应，长度: {len(response)} 字节")
                            print(f"角色选择响应内容: {response.hex()}")

                            # 解析响应内容
                            try:
                                content = extract_packet_content(response.hex())
                                cleaned_content = content.get('cleaned_content', '无法解析内容')
                                packet_type = content.get('packet_type', 'other')
                                print(f"解析角色选择响应: {cleaned_content}")

                                # 检查是否为登录异常
                                if '登录异常' in cleaned_content or '请重新登录' in cleaned_content:
                                    print(f"检测到登录异常响应: {cleaned_content}")
                                    print("关闭连接")
                                    self.current_sock.close()
                                    self.root.after(0, lambda: self._on_server_connect_failed("登录异常，无法进入游戏"))
                                    return

                                # 如果是角色选择响应，可以在UI上显示相应信息
                                if packet_type == 'role_selection' and cleaned_content:
                                    print(f"收到角色选择响应: {cleaned_content}")
                                    # 在聊天窗口显示角色选择响应
                                    if hasattr(self, 'message_text'):
                                        self.message_text.config(state=tk.NORMAL)
                                        self.message_text.insert(tk.END, f"系统提示: {cleaned_content}\n", "system")
                                        self.message_text.see(tk.END)
                                        self.message_text.config(state=tk.DISABLED)
                            except Exception as parse_error:
                                print(f"解析角色选择响应失败: {str(parse_error)}")
                            # 这样角色信息就会被正确解析并显示到UI上
                            self._on_receive_packet(response)

                            # 获取角色名称并打印欢迎信息
                            print(f"欢迎您!!! {self.current_role['role_name_cn']}")
                            # 打印背包列表
                            print(self.backpack_items)

                            packet = binascii.unhexlify('21000000e8030a000904cf07f605150400000f000000000000000000000400333432330000')
                            sent = self.current_sock.send(packet)

                            # 设置为1秒超时，便于后续接收循环
                            self.current_sock.settimeout(1)
                            print(f"成功设置套接字超时为1秒，准备持续接收报文")

                            # 启动接收报文的线程
                            receive_thread = start_receive_thread(
                                self.current_sock,
                                self._on_receive_packet  # 回调函数
                            )

                            # 在主线程中更新UI和状态
                            self.root.after(0,
                                            lambda: self._on_server_connect_success(self.current_sock, receive_thread))
                        else:
                            print("没有可用的socket对象，尝试重新连接")

                            # 使用已保存的socket对象，或者让函数创建新的socket
                            game_socket = connect_game_server(
                                self.current_session_id,
                                self.current_server_ip,
                                self.current_server_port,
                                self.current_role['role_id'],
                                self.current_role['role_index'],
                                getattr(self, 'current_sock', None)  # 传递已保存的socket对象
                            )

                            if game_socket:
                                print("成功连接到游戏服务器，开始接收报文")

                                # 启动接收报文的线程
                                receive_thread = start_receive_thread(
                                    game_socket,
                                    self._on_receive_packet  # 回调函数
                                )

                                # 在主线程中更新UI和状态
                                self.root.after(0, lambda: self._on_server_connect_success(game_socket, receive_thread))
                            else:
                                print("连接游戏服务器失败")
                                # 在主线程中更新UI和状态
                                self.root.after(0, lambda: self._on_server_connect_failed("连接游戏服务器失败"))
                    except Exception as e:
                        # 记录错误日志
                        error_msg = f"连接游戏服务器时出错: {str(e)}"
                        print(error_msg)
                        # 在主线程中更新UI和状态
                        self.root.after(0, lambda: self._on_server_connect_failed(error_msg))

                # 启动后台线程连接服务器
                self.connect_thread = threading.Thread(target=connect_server_thread, daemon=True)
                self.connect_thread.start()

            except Exception as e:
                # 记录错误日志
                error_msg = f"显示游戏主界面失败: {str(e)}"
                print(error_msg)
                # 更新状态
                self.character_status_label.config(text=error_msg, foreground="red")
                # 启用进入游戏按钮
                self.enter_game_button.config(state='normal')
        else:
            self.character_status_label.config(text="请先选择一个角色", foreground="red")
            print("未选择角色，无法进入游戏")

    def _on_server_connect_success(self, game_socket, receive_thread):
        """服务器连接成功回调"""
        self.game_socket = game_socket
        self.receive_thread = receive_thread
        # 启动心跳线程
        self._start_heartbeat_thread()

    def _start_heartbeat_thread(self):
        """启动心跳线程"""
        # 创建心跳线程
        self.heartbeat_thread = threading.Thread(target=self._heartbeat_thread, daemon=True)
        # 启动心跳线程
        self.heartbeat_thread.start()
        print("成功启动心跳线程")

    def _stop_heartbeat_thread(self):
        """停止心跳线程"""
        self._heartbeat_running = False

    def _heartbeat_thread(self):
        """心跳线程，定期发送心跳报文"""
        self._heartbeat_running = True
        while self._heartbeat_running:
            try:
                if hasattr(self, 'game_socket') and self.game_socket and self._heartbeat_running:
                    heartbeat_packet = binascii.unhexlify("12000000e80302000504000015250204000000000000")
                    # self.game_socket.send(heartbeat_packet)
                time.sleep(5)
            except Exception as e:
                if self._heartbeat_running:
                    print(f"心跳线程异常: {str(e)}")
                break

    def _on_server_connect_failed(self, error_msg):
        """服务器连接失败回调"""
        print(error_msg)
        # 在聊天界面显示连接失败信息
        if hasattr(self, 'message_text'):
            self.message_text.config(state=tk.NORMAL)
            self.message_text.insert(tk.END, f"连接服务器失败: {error_msg}\n")
            self.message_text.see(tk.END)  # 滚动到最新消息
            self.message_text.config(state=tk.DISABLED)

    def _on_login_exception(self):
        """处理登录异常，要求重新登录的情况"""
        print("收到服务器返回的登录异常，要求重新登录")
        # 关闭游戏服务器连接
        if hasattr(self, 'game_socket') and self.game_socket:
            try:
                self.game_socket.close()
                print("成功关闭游戏服务器连接")
            except:
                pass

        # 显示错误信息
        error_msg = "登录异常，请重新登录"
        messagebox.showerror("登录异常", error_msg)

        # 返回到登录界面
        self.game_main_frame.pack_forget()
        self._show_login_frame()

        # 更新状态
        self.status_label.config(text=error_msg, foreground="red")

    def _send_ack(self, sock, seq_number):
        """发送ACK确认"""
        ack_packet = struct.pack('>I', seq_number)  # 序列号确认
        sock.send(ack_packet)

    def _receive_with_ack(self, sock):
        """接收报文并发送ACK"""
        while True:
            try:
                packet = self._receive_full_packet(sock)
                if packet:
                    seq_number = struct.unpack('>I', packet[:4])[0]  # 提取序列号
                    self.packet_queue.put(packet[4:])  # 去掉序列号部分
                    self._send_ack(sock, seq_number)  # 发送ACK
            except Exception as e:
                print(f"接收报文并发送ACK失败: {str(e)}")
                break

    def _send_decompose_equipment_request(self, item_id):
        """发送分解装备请求"""
        try:
            # 生成一个6位的16进制数字
            random_num = random.randint(0x100000, 0x1000000)
            random_num = hex(random_num)[2:].zfill(6)
            # 构造报文                1a000000e8030800412a   aee7f5         05462a000008000000  abaef40b0000 0000
            decompose_packet_hex = "1a000000e8030800412a" + random_num + "05462a000008000000" + item_id + "0000"
            # 加入发送队列
            self._enqueue_packet(decompose_packet_hex, priority=0)
        except Exception as e:
            print(f"发送分解装备请求失败: {str(e)}")

    # 丢弃物品
    def _on_drop_item(self, item_id=None):
        num = "01"
        if item_id is None:
            item_id = self.gift_type_var.get()
            item = self.get_item_by_id(item_id)
            quantity = item.quantity
            if quantity > 100:
                num = "64"
            else:
                num = str(hex(quantity))[2:].zfill(2)
            print(item)
        """发送丢弃物品请求"""
        try:
            # 1e000000e8030800040438fff5050d0400000c00000001 647600 000000000064 0000 丢100个
            # 生成一个4位的16进制数字
            random_num = random.randint(0x0000, 0xFFFF)
            random_num = hex(random_num)[2:].zfill(4)
            drop_packet_hex = "1e000000e80308000404" + random_num + "f5050d0400000c00000001" + item_id + "0000" + num + "0000"
            print("******"+drop_packet_hex+"******")
            self._enqueue_packet(drop_packet_hex, priority=1)
        except Exception as e:
            print(f"发送丢弃物品请求失败: {str(e)}")

    def auto_drop(self, packet_hex):
        #  星光石 神装武器卷轴 金油 玄铁矿石
        target_packets = ["42a000000000", "2fa000000000", "30a000000000", "32a000000000"]
        # 判断packet_hex中是否包含target_packets的子集
        for target_packet in target_packets:
            if target_packet in packet_hex:
                print("自动丢弃:" + target_packet)
                print(packet_hex)
                # self._on_drop_item(target_packet)

    def _parse_world_chat_message(self, packet_hex):
        """解析世界频道消息报文内容（增强版-支持系统公告和玩家消息）"""
        try:
            # 首先判断报文类型
            packet_length = len(packet_hex)

            # 分析报文结构，识别不同类型的消息

            # 方法1：寻找消息内容的起始标记
            chinese_start_markers = ["e38090"]  # 【世】小 世 系等开头

            message_start = -1
            found_marker = None

            # 查找中文标记
            for marker in chinese_start_markers:
                pos = packet_hex.find(marker)
                if pos != -1 and pos > 30:  # 确保在报文头部之后
                    message_start = pos
                    found_marker = marker
                    break

            # 如果找不到中文标记，尝试其他方法
            if message_start == -1:
                # 查找常见的消息内容特征
                content_indicators = ["e68c91e68898", "e587bae594aee", "e98791e5b881", "e69d90e69699"]  # 挑战 出售 金币 材料
                for indicator in content_indicators:
                    pos = packet_hex.find(indicator)
                    if pos != -1 and pos > 30:
                        # 向前搜索到有效的UTF-8起始位置
                        message_start = pos
                        while message_start > 30 and message_start % 2 == 0:
                            try:
                                # 检查当前位置是否是有效的UTF-8起始
                                test_slice = packet_hex[message_start:message_start + 6]
                                if len(test_slice) >= 6:
                                    binascii.unhexlify(test_slice).decode('utf-8')
                                    break
                            except:
                                pass
                            message_start -= 2
                        break

            # 如果还是找不到，使用保守的方法
            if message_start == -1:
                message_start = 40  # 默认起始位置
                # 寻找第一个有效的UTF-8序列
                max_search = min(len(packet_hex) - 6, 100)
                while message_start < max_search:
                    try:
                        test_slice = packet_hex[message_start:message_start + 6]
                        if len(test_slice) >= 6:
                            test_bytes = binascii.unhexlify(test_slice)
                            # 检查是否是有效的UTF-8起始字节
                            if test_bytes[0] & 0x80 == 0 or (test_bytes[0] & 0xE0) == 0xC0:
                                # 单字节或双字节UTF-8起始
                                message_start_backup = message_start
                                try:
                                    test_bytes.decode('utf-8')
                                    break
                                except:
                                    # 如果解码失败，继续搜索
                                    pass
                    except:
                        pass
                    message_start += 2

            if message_start >= len(packet_hex) - 4:
                print(f"⚠️ 无法找到有效消息起始位置，报文长度: {packet_length}")
                return None

            # 提取消息内容
            end_marker = packet_hex.find("ff000000", message_start)
            if end_marker != -1:
                message_hex = packet_hex[message_start:end_marker]
            else:
                message_hex = packet_hex[message_start:]

            # 清理消息内容
            while message_hex.endswith("00") and len(message_hex) > 2:
                message_hex = message_hex[:-2]

            if len(message_hex) < 4:
                return None

            # 尝试多种编码方式解析
            message_text = None

            # 首先尝试UTF-8
            try:
                message_bytes = binascii.unhexlify(message_hex)
                message_text = message_bytes.decode('utf-8')

                # 清理和验证
                message_text = message_text.strip('\x00\x01\x02\x03\x04\x05\x06\x07\x08\x09\x0a\x0b\x0c\x0d\x0e\x0f')
                message_text = message_text.strip()

                # 检查是否包含明显的乱码
                if '\ufffd' in message_text or len(message_text) < 1:
                    message_text = None

            except UnicodeDecodeError as utf8_error:
                print(f"⚠️ UTF-8解码失败: {str(utf8_error)}")
                print(f"   原始数据片段: {message_hex[:60]}...")

                # 尝试Latin-1作为后备方案（用于显示原始字节）
                try:
                    message_bytes = binascii.unhexlify(message_hex)
                    message_text = message_bytes.decode('latin-1')
                    # 标记为可能的乱码
                    message_text = f"[可能乱码] {message_text}"
                except Exception as latin_error:
                    print(f"❌ Latin-1解码也失败: {str(latin_error)}")
                    return None

            # 如果解析成功，进行内容验证
            if message_text:
                # 过滤掉纯控制字符或过短的消息
                if len(message_text.strip()) < 2:
                    print(f"⚠️ 消息内容过短被过滤: '{message_text}'")
                    return None

                # 检查是否是有效的中文内容
                chinese_chars = sum(1 for c in message_text if ord(c) > 127)
                if chinese_chars == 0 and not any(
                        keyword in message_text.lower() for keyword in ['system', 'notice', '公告']):
                    print(f"⚠️ 可能非中文内容被过滤: '{message_text[:30]}...'")
                    return None
                return message_text

            return None

        except Exception as e:
            print(f"❌ 解析世界频道消息时发生错误: {str(e)}")
            return None

    def _display_world_chat_message(self, time_str, message, type):
        """在世界聊天标签页显示消息"""
        try:
            if hasattr(self, 'world_chat_text'):
                self.world_chat_text.config(state=tk.NORMAL)

                # 格式化显示：[时间] 消息内容
                formatted_message = f"[{time_str}] {message}\n"

                # 插入消息
                self.world_chat_text.insert(tk.END, formatted_message, type)

                # 自动滚动到底部
                self.world_chat_text.see(tk.END)

                # 保持文本框为只读状态
                self.world_chat_text.config(state=tk.DISABLED)

                # 限制消息历史长度（防止内存占用过大）
                total_lines = int(self.world_chat_text.index('end-1c').split('.')[0])
                if total_lines > 500:  # 保留最近500行
                    # 删除前100行
                    self.world_chat_text.config(state=tk.NORMAL)
                    self.world_chat_text.delete('1.0', '100.0')
                    self.world_chat_text.config(state=tk.DISABLED)

        except Exception as e:
            print(f"显示世界频道消息时发生错误: {str(e)}")

    def get_item_by_id(self, item_id):
        """根据item_id查找物品对象"""
        return self.backpack_items.get(item_id, None)

    def _on_receive_packet(self, packet):
        with self.lock:
            """处理接收到的游戏报文，每个报文都解析处理一遍"""
            # 获取当前时间
            current_time = time.time()
            # 格式化时间为字符串，格式：YYYY-MM-DD HH:MM:SS
            time_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(current_time))
            # 短格式时间，用于聊天显示，格式：HH:MM:SS
            time.strftime("%H:%M:%S", time.localtime(current_time))

            packet_hex = packet.hex()

            # 获取报文标识符
            packet_cmd = packet_hex[8:20]
            # 检查是否需要忽略的报文（从第9位到第20位是e8030100514f）
            if len(packet_hex) <= 20 or packet_cmd == "e8030100514f":
                return  # 直接返回，不处理该报文

            # 打印原始报文，加上时间戳
            print(f"[{time_str}] 原始报文: {packet_hex}")
            content = extract_packet_content(packet_hex)
            # 检查是否是背包信息报文（从第9位到第20位是e8030100ec07）
            # 注意：Python索引从0开始，所以第9位是索引8，第20位是索引19
            if packet_cmd == "e8030100ec07":
                self.auto_drop(packet_hex)
                # 解析报文内容
                try:
                    if 'cleaned_content' in content and content['cleaned_content']:
                        cleaned_content = content['cleaned_content'][content['cleaned_content'].find("已使用"):]
                        # 在主线程中更新背包页面显示
                        self.root.after(0, self._update_backpack_display)
                        print(f"[{time_str}] 内容: {cleaned_content}")
                        # # 获取获得的物品Id 即报文中 100cd00后面的
                        index = packet_hex.find("100cd00")
                        self.message_text.config(state=tk.NORMAL)
                        msg = f"[{time_str}]" + cleaned_content
                        self.message_text.insert(tk.END, msg + "\n", "system")
                        self.message_text.see(tk.END)
                        self.message_text.config(state=tk.DISABLED)
                        if index != -1:
                            item_id = packet_hex[index + 7:index + 7 + 12]
                            item_name_b = packet_hex[packet_hex.find("005331") + 2:packet_hex.find("f020a0000")]
                            item_name = binascii.unhexlify(item_name_b).decode('utf-8')
                            print(f"[{time_str}] 装备Id: {item_id} 名字: {item_name}")
                            current_job = self.current_role["role_job"]
                            is_golden = "黄金" in item_name
                            # # 职业到本职业战甲/头盔名称的映射
                            job_armor_map = {
                                "侠客": ["侠士战甲", "侠士头盔"],
                                "刺客": ["刺客战甲", "刺客头盔"],
                                "术士": ["术士战甲", "术士头盔"],
                            }
                            # # 获取本职业不允许分解的装备名称列表
                            protected_items = job_armor_map.get(current_job, [])
                            #
                            # # 判断是否为本命战甲或头盔（本职业专用装备不允许分解）
                            is_zj = any(
                                armor_name in item_name for armor_name in protected_items if "战甲" in armor_name)
                            is_th = any(
                                armor_name in item_name for armor_name in protected_items if "头盔" in armor_name)
                            #
                            is_valid_item = (
                                    "e88eb7e5be97efbc9a2f5331" in packet_hex and
                                    not is_golden and not is_zj and not is_th
                            )
                            if is_valid_item:
                                # 分解装备
                                self._send_decompose_equipment_request(item_id)
                except Exception as e:
                    print(f"[{time_str}] 解析背包报文失败: {str(e)}")
                    #         打印异常
                    print(f"[{time_str}] 错误: {str(e)}")
            elif packet_cmd == "e8030100ed07":
                self.auto_drop(packet_hex)
                # 获取获得的物品Id 即报文中 100cd00后面的
                index = packet_hex.find("100cd00")
                if index != -1:
                    item_id = packet_hex[index + 7:index + 7 + 12]
                    # 获取装备名字截取从报文 e005331 位置到报文 f020a0000之前的坯片段 并转成utf-8 编码
                    item_name_b = packet_hex[packet_hex.find("005331") + 2:packet_hex.find("f020a0000")]
                    item_name = binascii.unhexlify(item_name_b).decode('utf-8')
                    print(f"[{time_str}] 装备Id: {item_id} 名字: {item_name}")
                    current_job = self.current_role["role_job"]
                    is_golden = "黄金" in item_name

                    # 职业到本职业战甲/头盔名称的映射
                    job_armor_map = {
                        "侠客": ["侠士战甲", "侠士头盔"],
                        "刺客": ["刺客战甲", "刺客头盔"],
                        "术士": ["术士战甲", "术士头盔"],
                    }

                    # 获取本职业不允许分解的装备名称列表
                    protected_items = job_armor_map.get(current_job, [])

                    # 判断是否为本命战甲或头盔（本职业专用装备不允许分解）
                    is_zj = any(armor_name in item_name for armor_name in protected_items if "战甲" in armor_name)
                    is_th = any(armor_name in item_name for armor_name in protected_items if "头盔" in armor_name)

                    is_valid_item = (
                            not is_golden and not is_zj and not is_th
                    )
                    if is_valid_item:
                        if "战甲" in item_name or "头盔" in item_name or "黄金" in item_name:
                            print(f"职业: {current_job}")
                            print(f"黄金装备:{is_golden}")
                            print(f"本命甲: {is_golden}")
                            print(f"本命头: {is_golden}")
                        # 分解装备
                        self._send_decompose_equipment_request(item_id)
                    else:
                        print(f"[{time_str}] 无效物品: {item_name}")
                else:
                    print(f"[{time_str}] 未获取物品Id {packet_hex}")
            elif packet_cmd == "e8030100f207":
                if "e7b3bbe7bb9fe585ace5918a" in packet_hex:
                    # 从报文e7b3bbe7bb9fe585ace5918a位置截取到810084ff000000前的报文片段转成utf-8编码
                    world_message = binascii.unhexlify(packet_hex[
                                                       packet_hex.find("e7b3bbe7bb9fe585ace5918a"):packet_hex.find(
                                                           "ff000000") - 4]).decode('utf-8')
                    print(f"[{time_str}] {world_message}")
                    self._display_world_chat_message(time_str, world_message, "system_announcement")
                else:
                    # 处理世界频道消息报文   从e38090截取到810084ff000000前的报文片段转成utf-8编码
                    try:
                        # 解析世界频道消息内容
                        world_message = binascii.unhexlify(packet_hex[
                                                           packet_hex.find("e38090"):packet_hex.find(
                                                               "ff000000") - 4]).decode('utf-8')
                        if world_message:
                            # 显示到世界聊天标签页
                            self._display_world_chat_message(time_str, world_message, "world_chat")
                            print(f"[{time_str}]频道消息: {world_message}")
                    except Exception as e:
                        print(f"[{time_str}] 解析世界频道消息失败: {str(e)}")
                        # 显示原始报文到报文记录页面
                        if hasattr(self, 'packet_log_text'):
                            self.packet_log_text.config(state=tk.NORMAL)
                            self.packet_log_text.insert(tk.END, f"[{time_str}] 未解析的世界频道报文: {packet_hex}\n")
                            self.packet_log_text.see(tk.END)
                            self.packet_log_text.config(state=tk.DISABLED)
            elif packet_cmd == "e80301005151":
                # 获取报文中第一次出现e588位置开始截取到报文中第二次出现2f的报文
                # 查找第一次出现 e588 的位置
                start_index = packet_hex.find("e588")
                if start_index == -1:
                    return None  # 如果没有找到 e588，返回 None

                # 从第一次出现 e588 的位置之后查找第一次出现 2f 的位置
                first_2f_index = packet_hex.find("2f", start_index + 4)  # 从 e588 后面开始查找
                if first_2f_index == -1:
                    return None  # 如果没有找到第一个 2f，返回 None

                # 从第一个 2f 之后查找第二个 2f 的位置
                second_2f_index = packet_hex.find("2f", first_2f_index + 2)
                if second_2f_index == -1:
                    return None  # 如果没有找到第二个 2f，返回 None

                # 截取从 e588 到第二个 2f 之间的内容
                sub_packet = packet_hex[start_index:second_2f_index]
                #   将报文转成utf-8编码
                sub_packet_utf8 = binascii.unhexlify(sub_packet).decode('utf-8')

                # 打印到综合聊天框
                self.message_text.config(state=tk.NORMAL)
                self.message_text.insert(tk.END, f"[{time_str}] {sub_packet_utf8}\n", "system")
                self.message_text.see(tk.END)
                self.message_text.config(state=tk.DISABLED)

                print(f"[{time_str}] 获取物品信息报文: {sub_packet_utf8}")
            elif packet_cmd == "e8030100d607":
                # 其他物品
                self.get_items_info(packet_hex, "ce00")
                # 装备
                self.get_items_info(packet_hex, "cd00")
            # 检查是否是背包物品信息报文（d607命令）
            elif packet_hex[8:16] == "d6070100":
                print(f"[{time_str}] 检测到d607背包物品信息报文")
                # 解析报文内容
                try:
                    content = extract_packet_content(packet.hex())
                    if isinstance(content, dict):
                        if 'treasure_boxes' in content:
                            # 这是专门的宝箱信息报文
                            treasure_info = content['treasure_boxes']
                            print(f"[{time_str}] 解析到宝箱信息: {treasure_info}")
                            # 格式化显示宝箱信息
                            display_text = "背包物品信息:\n"
                            for box_type, quantities in treasure_info.items():
                                display_text += f"{box_type}: {quantities}\n"

                            # 在主线程中更新背包页面显示
                            self.root.after(0, self._update_backpack_display)
                        elif 'cleaned_content' in content and content['cleaned_content']:
                            # 普通的d607报文内容
                            cleaned_content = content['cleaned_content']
                            print(f"[{time_str}] d607报文解析内容: {cleaned_content}")
                            # 在主线程中更新背包页面显示
                            self.root.after(0, self._update_backpack_display)
                except Exception as e:
                    print(f"[{time_str}] 解析d607报文失败: {str(e)}")
            # 线程安全地处理报文
            pass

    def get_items_info(self, packet_hex, item_type):
        if item_type == "cd00":
            can_disassemble = True
        else:
            can_disassemble = False
        # 获取报文中 所有的000ce00
        positions = find_all_positions(packet_hex, item_type)
        print(f"找到 {len(positions)} 个位置: {positions}")
        for position in positions:
            try:
                # 获取物品ID
                item_id = packet_hex[position + 4:position + 16]
                # 获取物品数量
                item_num = packet_hex[position + 17:position + 22]
                # 转成十进制
                item_num = int(item_num, 16)
                # 获取物品中文名 长度
                item_name_length = packet_hex[position + 32:position + 38]
                decimal_result = int(item_name_length, 16)
                # 截取中文名
                item_name = packet_hex[position + 40:position + 40 + decimal_result * 2]
                name_cn = binascii.unhexlify(item_name).decode('utf-8')
                new_item = Item(name=name_cn, quantity=item_num, item_id=item_id, disassemble=can_disassemble)
                self.backpack_items[item_id] = new_item
            except Exception as e:
                print(f"解析物品信息失败: {str(e)}")
        self.root.after(0, self._update_backpack_display)

    def create_chat_page(self, parent):
        """创建聊天页面"""
        # 创建聊天区域
        chat_frame = ttk.Frame(parent)
        chat_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        # 消息显示区域
        self.message_text = tk.Text(chat_frame, height=20, wrap=tk.WORD)

        # 创建颜色标签
        self.message_text.tag_configure("world_chat", foreground="red")  # 世界频道消息显示为红色
        self.message_text.tag_configure("system", foreground="blue")  # 系统消息显示为蓝色
        self.message_text.tag_configure("normal", foreground="black")  # 普通消息显示为黑色
        self.message_text.tag_configure("system_announcement", foreground="#FFA500")  # 系统公告显示为橙色

        self.message_text.pack(fill=tk.BOTH, expand=True, side=tk.TOP)

        # 滚动条
        scrollbar = ttk.Scrollbar(self.message_text, command=self.message_text.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.message_text.config(yscrollcommand=scrollbar.set)

        # 消息输入区域
        input_frame = ttk.Frame(chat_frame)
        input_frame.pack(fill=tk.X, side=tk.BOTTOM, pady=5)

        self.message_entry = ttk.Entry(input_frame)
        self.message_entry.pack(fill=tk.X, side=tk.LEFT, expand=True, padx=5)
        # 绑定回车键发送消息
        self.message_entry.bind('<Return>', self._on_send_message)

        self.send_button = ttk.Button(input_frame, text="发送", width=10, command=self._on_send_message)
        self.send_button.pack(side=tk.RIGHT, padx=5)

        # 初始消息
        self.message_text.insert(tk.END, "欢迎来到游戏聊天频道！\n", "system")
        self.message_text.config(state=tk.DISABLED)

        # 注意：返回按钮现在由 bottom_button_frame 统一管理，不需要在这里处理

    def _return_to_home(self):
        """返回首页按钮点击事件"""
        try:
            # 断开当前所有 socket 连接
            if hasattr(self, 'current_sock') and self.current_sock:
                self.current_sock.close()
                print("已断开当前 socket 连接")

            if hasattr(self, 'game_socket') and self.game_socket:
                self.game_socket.close()
                print("已断开游戏服务器 socket 连接")

            # 清除所有队列数据
            while not self.send_queue.empty():
                try:
                    self.send_queue.get_nowait()
                except queue.Empty:
                    break
            print("已清空发送队列")

            while not self.packet_queue.empty():
                try:
                    self.packet_queue.get_nowait()
                except queue.Empty:
                    break
            print("已清空接收队列")

            # 清除背包物品
            self.backpack_items.clear()
            print("已清空背包物品")

            # 返回登录页面
            self.game_main_frame.pack_forget()
            self._show_login_frame()
            self.current_role = None  # 👈 关键！确保状态干净
            self.current_session_id = None
            # 更新状态
            self.status_label.config(text="已返回登录页面", foreground="green")
        except Exception as e:
            print(f"返回首页时发生错误: {str(e)}")

    def create_world_chat_page(self, parent):
        """创建世界聊天页面"""
        # 创建世界聊天区域
        world_chat_frame = ttk.Frame(parent)
        world_chat_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        # 消息显示区域
        self.world_chat_text = tk.Text(world_chat_frame, height=20, wrap=tk.WORD)

        # 创建颜色标签
        self.world_chat_text.tag_configure("world_chat", foreground="red")  # 世界频道消息显示为红色
        self.world_chat_text.tag_configure("system_announcement", foreground="#FFA500")  # 系统公告显示为橙色

        self.world_chat_text.pack(fill=tk.BOTH, expand=True, side=tk.TOP)

        # 滚动条
        scrollbar = ttk.Scrollbar(self.world_chat_text, command=self.world_chat_text.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.world_chat_text.config(yscrollcommand=scrollbar.set)

        # 初始消息
        self.world_chat_text.insert(tk.END, "欢迎来到世界聊天频道！\n")
        self.world_chat_text.config(state=tk.DISABLED)

        # 注意：返回按钮现在由 bottom_button_frame 统一管理

    def create_packet_log_page(self, parent):
        """创建报文记录页面"""
        # 创建报文记录区域
        packet_log_frame = ttk.Frame(parent)
        packet_log_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        # 报文显示区域
        self.packet_log_text = tk.Text(packet_log_frame, height=20, wrap=tk.NONE)  # 不自动换行，便于查看长报文

        # 创建颜色标签
        self.packet_log_text.tag_configure("header", foreground="blue", font=(".Helvetica", 10, "bold"))  # 报文头部显示为蓝色加粗
        self.packet_log_text.tag_configure("packet", foreground="black")  # 普通报文显示为黑色
        self.packet_log_text.tag_configure("content", foreground="green")  # 解析后的内容显示为绿色

        # 添加水平滚动条
        h_scrollbar = ttk.Scrollbar(packet_log_frame, orient=tk.HORIZONTAL, command=self.packet_log_text.xview)
        h_scrollbar.pack(side=tk.BOTTOM, fill=tk.X)

        # 添加垂直滚动条
        v_scrollbar = ttk.Scrollbar(packet_log_frame, orient=tk.VERTICAL, command=self.packet_log_text.yview)
        v_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        self.packet_log_text.pack(fill=tk.BOTH, expand=True, side=tk.TOP)
        self.packet_log_text.config(xscrollcommand=h_scrollbar.set, yscrollcommand=v_scrollbar.set)

        # 初始消息
        self.packet_log_text.insert(tk.END, "欢迎来到报文记录频道！\n", "header")
        self.packet_log_text.insert(tk.END, "所有收到的游戏报文将显示在这里...\n\n")
        self.packet_log_text.config(state=tk.DISABLED)

        # 添加返回首页按钮
        return_button_frame = ttk.Frame(packet_log_frame)
        return_button_frame.pack(fill=tk.X, side=tk.BOTTOM, pady=5)
        self.return_home_button = ttk.Button(return_button_frame, text="返回首页", width=10,
                                             command=self._return_to_home)
        self.return_home_button.pack(side=tk.RIGHT, padx=5)

    def _on_login_failed(self, error_msg):
        """登录失败回调（使用现代化对话框）"""
        self.progress.stop()
        self.progress.grid_remove()
        self.login_button.config(state='normal')
        self.status_label.config(text=f"登录失败: {error_msg}", foreground="red")

        # 显示错误对话框
        self._show_modern_dialog(
            title="登录失败",
            message=error_msg,
            dialog_type="error",
            buttons=["重试", "取消"]
        )

    def _on_send_message(self, event=None):
        """发送消息"""
        try:
            # 获取用户输入的消息
            message = self.message_entry.get().strip()
            if not message:
                return

            # 检查是否有可用的socket连接
            if not hasattr(self, 'current_sock') or not self.current_sock:
                # 显示错误信息
                if hasattr(self, 'message_text'):
                    self.message_text.config(state=tk.NORMAL)
                    self.message_text.insert(tk.END, f"发送失败：未连接到游戏服务器\n", "system")
                    self.message_text.see(tk.END)
                    self.message_text.config(state=tk.DISABLED)
                return

            # 使用用户提供的聊天请求报文作为模板
            # 模板：29000000e8030a00090470fef5051d0400001700000000000000000000000c00e78ea9e584bfe78ea9e584bf0000
            # 解析模板：
            # 前28字节是固定头部：29000000e8030a00090470fef5051d040000170000000000000000000000
            # 然后是消息内容的长度字段：0c00（小端序，即12字节）
            # 然后是消息内容：e78ea9e584bfe78ea9e584bf0000（"玩家玩家"的hex）

            # 构建消息内容的hex
            message_hex = message.encode('utf-8').hex()
            # 计算消息内容的长度（小端序，2字节）
            message_len = len(message_hex) // 2  # 转换为字节数
            message_len_hex = f"{message_len:04x}"  # 转换为4位十六进制字符串
            message_len_hex = message_len_hex[2:] + message_len_hex[:2]  # 转换为小端序

            # 构建完整的请求报文
            # 固定头部（28字节）：29000000e8030a00090470fef5051d040000170000000000000000000000
            # 消息长度（2字节，小端序）：message_len_hex
            # 消息内容：message_hex
            # 结尾填充：0000
            packet_hex = f"29000000e8030a00090470fef5051d040000170000000000000000000000{message_len_hex}{message_hex}0000"

            # 转换为字节
            packet = binascii.unhexlify(packet_hex)

            # 发送报文
            sent = self.current_sock.send(packet)
            print(f"发送聊天请求成功，发送字节数: {sent}")
            print(f"发送报文: {packet_hex}")

            # 清空输入框
            self.message_entry.delete(0, tk.END)

            # 在聊天窗口显示自己发送的消息
            if hasattr(self, 'message_text'):
                self.message_text.config(state=tk.NORMAL)
                self.message_text.insert(tk.END, f"我: {message}\n", "normal")
                self.message_text.see(tk.END)
                self.message_text.config(state=tk.DISABLED)

            # 如果是世界聊天，也显示在世界聊天窗口
            if hasattr(self, 'world_chat_text'):
                self.world_chat_text.config(state=tk.NORMAL)
                self.world_chat_text.insert(tk.END, f"我: {message}\n", "world_chat")
                self.world_chat_text.see(tk.END)
                self.world_chat_text.config(state=tk.DISABLED)

        except Exception as e:
            print(f"发送消息失败: {str(e)}")
            # 显示错误信息
            if hasattr(self, 'message_text'):
                self.message_text.config(state=tk.NORMAL)
                self.message_text.insert(tk.END, f"发送失败: {str(e)}\n", "system")
                self.message_text.see(tk.END)
                self.message_text.config(state=tk.DISABLED)

    def create_announcement_page(self, parent):
        """创建公告页面"""
        # 创建公告区域
        announcement_frame = ttk.Frame(parent)
        announcement_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        # 公告内容
        announcement_text = tk.Text(announcement_frame, wrap=tk.WORD)
        announcement_text.pack(fill=tk.BOTH, expand=True, side=tk.TOP)

        # 滚动条
        scrollbar = ttk.Scrollbar(announcement_text, command=announcement_text.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        announcement_text.config(yscrollcommand=scrollbar.set)

        # 初始公告内容
        announcement_text.insert(tk.END, "1月21日维护公告\n")
        announcement_text.insert(tk.END, "维护时间：9:30-10:30，如遇意外情况可能会延长更新时间\n")
        announcement_text.insert(tk.END, "维护范围：全服\n")
        announcement_text.insert(tk.END, "维护内容：\n")
        announcement_text.insert(tk.END,
                                 "为保证服务器的运行稳定和服务质量，服务器预计将会在9:30-10:30时段停机例行维护，请您注意维护时间，在维护开始前，退出游戏并确认关闭游戏进程，以避免出现不必要的损失。\n")
        announcement_text.insert(tk.END, "更多内容加Q群看，群号：290172032\n")
        announcement_text.config(state=tk.DISABLED)

    def create_backpack_page(self, parent):
        """创建背包页面"""
        # 创建背包区域
        backpack_frame = ttk.Frame(parent)
        backpack_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        # 背包操作按钮区域（紧凑布局）
        top_frame = ttk.Frame(backpack_frame)
        top_frame.pack(fill=tk.X, side=tk.TOP, pady=(0, 5))

        # 小按钮
        refresh_btn = ttk.Button(top_frame, text="🔄", width=3, command=self._refresh_backpack_manual)
        refresh_btn.pack(side=tk.LEFT, padx=2)

        self.drop_item_button = ttk.Button(top_frame, text="丢弃", width=4, command=self._on_drop_item)
        self.drop_item_button.pack(side=tk.LEFT, padx=2)

        self.use_item_button = ttk.Button(top_frame, text="使用", width=4, command=self._on_use_item)
        self.use_item_button.pack(side=tk.LEFT, padx=2)
        # 分解按钮
        self.decompose_button = ttk.Button(top_frame, text="分解", width=4, command=self._on_decompose)
        self.decompose_button.pack(side=tk.LEFT, padx=2)
        # 一键分解
        self.one_key_decompose_button = ttk.Button(top_frame, text="一键分解", width=8,
                                                   command=self._on_one_key_decompose)
        self.one_key_decompose_button.pack(side=tk.LEFT, padx=2)

        self.gift_button = ttk.Button(top_frame, text="兑换五灵", width=8, command=self._on_exchange_wl)
        self.gift_button.pack(side=tk.LEFT, padx=2)

        # 礼包类型选择框（动态生成）- 水平布局
        gift_frame = ttk.Frame(top_frame)
        gift_frame.pack(side=tk.RIGHT, padx=2)

        # 物品显示区域（水平布局）
        items_frame = ttk.Frame(backpack_frame)
        items_frame.pack(fill=tk.BOTH, expand=True, side=tk.TOP, pady=(0, 5))

        # 物品选择按钮容器（水平排列）
        self.gift_frame = ttk.Frame(items_frame)
        self.gift_frame.pack(fill=tk.BOTH, expand=True)

    def create_warehouse_page(self, parent):
        """创建仓库页面"""
        # 创建仓库区域
        warehouse_frame = ttk.Frame(parent)
        warehouse_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        # 仓库标题
        title_label = ttk.Label(warehouse_frame, text="仓库", font=("Arial", 16, "bold"))
        title_label.pack(side=tk.TOP, pady=10)

        # 仓库格子区域
        grid_frame = ttk.Frame(warehouse_frame)
        grid_frame.pack(fill=tk.BOTH, expand=True, side=tk.TOP)
        # 仓库信息
        info_frame = ttk.Frame(warehouse_frame)
        info_frame.pack(fill=tk.X, side=tk.BOTTOM, pady=10)

        ttk.Label(info_frame, text="仓库容量: 80/80").pack(side=tk.LEFT, padx=5)
        ttk.Button(info_frame, text="整理仓库", width=10).pack(side=tk.RIGHT, padx=5)

        # 添加返回首页按钮
        return_button_frame = ttk.Frame(warehouse_frame)
        return_button_frame.pack(fill=tk.X, side=tk.BOTTOM, pady=5)
        self.return_home_button = ttk.Button(return_button_frame, text="返回首页", width=10,
                                             command=self._return_to_home)
        self.return_home_button.pack(side=tk.RIGHT, padx=5)

    def _show_help_dialog(self):
        """显示帮助信息对话框"""
        help_text = (
            "🎮 游戏助手帮助\n\n"
            "🏠 首页：返回登录页面\n"
            "🔁 重连：自动重新登录游戏\n"
            "📦 使用物品：快速使用选中的物品\n"
            "🗑️ 丢弃物品：快速丢弃选中的物品\n"
            "❓ 帮助：显示此帮助信息"
        )
        self.show_info("帮助", help_text)

    def _create_global_toolbar(self):
        """创建全局工具栏（在顶部）"""
        # 创建工具栏框架
        self.toolbar_frame = ttk.Frame(self.game_main_frame, padding="5")
        # 工具栏显示在顶部
        self.toolbar_frame.pack(side=tk.TOP, fill=tk.X, pady=(0, 5))

        # 设置工具栏样式
        self.style.configure("Toolbar.TButton", font=("Arial", 10, "bold"), padding=5)

        # 添加常用按钮
        self.home_button = ttk.Button(
            self.toolbar_frame,
            text="🏠 首页",
            style="Toolbar.TButton",
            command=self._return_to_home
        )
        self.home_button.pack(side=tk.LEFT, padx=5)

        self.reconnect_button = ttk.Button(
            self.toolbar_frame,
            text="🔁 重连",
            style="Toolbar.TButton",
            command=self._trigger_reconnect
        )
        self.reconnect_button.pack(side=tk.LEFT, padx=5)

        # 重连状态标签
        self.reconnect_status_label = ttk.Label(
            self.toolbar_frame,
            text="",
            foreground="blue"
        )
        self.reconnect_status_label.pack(side=tk.LEFT, padx=10)

        self.use_item_button_toolbar = ttk.Button(
            self.toolbar_frame,
            text="📦 使用物品",
            style="Toolbar.TButton",
            command=self._on_use_item
        )
        self.use_item_button_toolbar.pack(side=tk.LEFT, padx=5)

        self.drop_item_button_toolbar = ttk.Button(
            self.toolbar_frame,
            text="🗑️ 丢弃物品",
            style="Toolbar.TButton",
            command=self._on_drop_item
        )
        self.drop_item_button_toolbar.pack(side=tk.LEFT, padx=5)

        self.help_button = ttk.Button(
            self.toolbar_frame,
            text="❓ 帮助",
            style="Toolbar.TButton",
            command=self._show_help_dialog
        )
        self.help_button.pack(side=tk.RIGHT, padx=5)

    def create_role_info_page(self, parent):
        """创建角色信息页面"""
        # 创建角色信息区域
        role_info_frame = ttk.Frame(parent)
        role_info_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        # 发起组队按钮
        self.team_button = ttk.Button(role_info_frame, text="发起组队", width=10, command=self._on_team)
        self.team_button.pack(side=tk.LEFT, padx=5)

        # 角色基本信息
        basic_info_frame = ttk.LabelFrame(role_info_frame, text="角色基本信息", padding="10")
        basic_info_frame.pack(fill=tk.X, side=tk.TOP, pady=10)

        # 角色名称
        ttk.Label(basic_info_frame, text="角色名称:").grid(row=0, column=0, padx=10, pady=5, sticky=tk.W)
        self.role_name_label = ttk.Label(basic_info_frame,
                                         text=self.current_role['role_name_cn'] if hasattr(self,
                                                                                           'current_role') and self.current_role else "")
        self.role_name_label.grid(row=0, column=1, padx=10, pady=5, sticky=tk.W)

        # 角色等级
        ttk.Label(basic_info_frame, text="角色等级:").grid(row=0, column=2, padx=10, pady=5, sticky=tk.W)
        self.role_level_label = ttk.Label(basic_info_frame, text="0")
        self.role_level_label.grid(row=0, column=3, padx=10, pady=5, sticky=tk.W)

        # 角色职业
        ttk.Label(basic_info_frame, text="角色职业:" + self.current_role["role_job"]).grid(row=1, column=0,
                                                                                           padx=10,
                                                                                           pady=5, sticky=tk.W)
        self.role_class_label = ttk.Label(basic_info_frame, text="")
        self.role_class_label.grid(row=1, column=1, padx=10, pady=5, sticky=tk.W)

        # 角色性别
        ttk.Label(basic_info_frame, text="角色性别:").grid(row=1, column=2, padx=10, pady=5, sticky=tk.W)
        self.role_gender_label = ttk.Label(basic_info_frame, text="")
        self.role_gender_label.grid(row=1, column=3, padx=10, pady=5, sticky=tk.W)

        # 角色属性
        attr_frame = ttk.LabelFrame(role_info_frame, text="角色属性", padding="10")
        attr_frame.pack(fill=tk.X, side=tk.TOP, pady=10)

        # 攻击力
        ttk.Label(attr_frame, text="攻击力:").grid(row=0, column=0, padx=10, pady=5, sticky=tk.W)
        self.attack_label = ttk.Label(attr_frame, text="0")
        self.attack_label.grid(row=0, column=1, padx=10, pady=5, sticky=tk.W)

        # 防御力
        ttk.Label(attr_frame, text="防御力:").grid(row=0, column=2, padx=10, pady=5, sticky=tk.W)
        self.defense_label = ttk.Label(attr_frame, text="0")
        self.defense_label.grid(row=0, column=3, padx=10, pady=5, sticky=tk.W)

        # 生命值
        ttk.Label(attr_frame, text="生命值:").grid(row=1, column=0, padx=10, pady=5, sticky=tk.W)
        self.hp_label = ttk.Label(attr_frame, text="0")
        self.hp_label.grid(row=1, column=1, padx=10, pady=5, sticky=tk.W)

        # 内力值
        ttk.Label(attr_frame, text="内力值:").grid(row=1, column=2, padx=10, pady=5, sticky=tk.W)
        self.mp_label = ttk.Label(attr_frame, text="0")
        self.mp_label.grid(row=1, column=3, padx=10, pady=5, sticky=tk.W)

        # 角色装备
        equip_frame = ttk.LabelFrame(role_info_frame, text="角色装备", padding="10")
        equip_frame.pack(fill=tk.BOTH, expand=True, side=tk.TOP, pady=10)

        # 添加返回首页按钮
        return_button_frame = ttk.Frame(role_info_frame)
        return_button_frame.pack(fill=tk.X, side=tk.BOTTOM, pady=5)
        self.return_home_button = ttk.Button(return_button_frame, text="返回首页", width=10,
                                             command=self._return_to_home)
        self.return_home_button.pack(side=tk.RIGHT, padx=5)

    def _on_use_item(self):
        with self.lock:
            """使用物品按钮点击事件"""
            try:
                # 检查是否有可用的socket连接
                if not hasattr(self, 'current_sock') or not self.current_sock:
                    if hasattr(self, 'message_text'):
                        self.message_text.config(state=tk.NORMAL)
                        self.message_text.insert(tk.END, f"使用物品失败：未连接到游戏服务器\n", "system")
                        self.message_text.see(tk.END)
                        self.message_text.config(state=tk.DISABLED)
                    return

                # 获取当前选中的礼包类型值
                selected_gift = self.gift_type_var.get()
                if not selected_gift:
                    return

                item = self.get_item_by_id(selected_gift)
                print(item)
                if item.quantity == 0:
                    item.quantity = 1
                # 循环使用
                while item.quantity > 0:
                    item.quantity -= 1
                    # 生成一个6位的16进制数字
                    random_num = random.randint(0x100000, 0x1000000)
                    random_num = hex(random_num)[2:].zfill(6)
                    # 构造报文               1e000000e80308000404    6ffdf5        050d0400000c00000000     a51f00    0000000000010000
                    use_item_packet_hex = "1e000000e80308000404" + random_num + "050d0400000c00000000" + selected_gift + "0000010000"
                    # 加入发送队列
                    self._enqueue_packet(use_item_packet_hex)
            except Exception as e:
                print(f"使用物品失败: {str(e)}")
                if hasattr(self, 'message_text'):
                    self.message_text.config(state=tk.NORMAL)
                    self.message_text.insert(tk.END, f"使用物品失败: {str(e)}\n", "system")
                    self.message_text.see(tk.END)
                    self.message_text.config(state=tk.DISABLED)

    def _on_decompose(self):
        with self.lock:
            selected_gift = self.gift_type_var.get()
            if not selected_gift:
                return
            self._send_decompose_equipment_request(selected_gift)

    def _on_one_key_decompose(self):
        with self.lock:
            for item_id, item in self.backpack_items.items():
                if item.disassemble:
                    # 获取当前角色职业
                    current_job = self.current_role["role_job"]
                    is_golden = "黄金" in item.name
                    # # 职业到本职业战甲/头盔名称的映射
                    job_armor_map = {
                        "侠客": ["侠士战甲", "侠士头盔"],
                        "刺客": ["刺客战甲", "刺客头盔"],
                        "术士": ["术士战甲", "术士头盔"],
                    }
                    # # 获取本职业不允许分解的装备名称列表
                    protected_items = job_armor_map.get(current_job, [])
                    #  判断是否为本命战甲或头盔（本职业专用装备不允许分解）
                    is_zj = any(
                        armor_name in item.name for armor_name in protected_items if "战甲" in armor_name)
                    is_th = any(
                        armor_name in item.name for armor_name in protected_items if "头盔" in armor_name)
                    is_valid_item = (
                            not is_golden and not is_zj and not is_th
                    )
                    if is_valid_item:
                        print(f"正在分解: {item.name}")
                        self._send_decompose_equipment_request(item_id)
                    else:
                        print(f"{item.name} 本命不可分解")

    def _on_exchange_wl(self):
        with self.lock:
            """发送消息按钮点击事件"""
            try:
                # 生成一个4位的16进制数字
                random_num = hex(random.randint(0x1000, 0x10000))[2:].zfill(4)
                packet_hex = "27000000e8030d00fe03f5fff50510040000150000004a02000002069d0900000000000000000001000000"
                print(f"兑换五灵: {packet_hex}")
                self._enqueue_packet(packet_hex)
            except Exception as e:
                print(f"使用物品失败: {str(e)}")

    def _on_team(self):
        """使用物品按钮点击事件"""
        with self.lock:
            random_num = random.randint(0x100000, 0x1000000)
            random_num = hex(random_num)[2:].zfill(6)
            # 构造报文               1e000000e80308000404    6ffdf5        050d0400000c00000000     a51f00          0000000000010000
            # use_item_packet_hex = "18000000e803030044289605f6054728000006000000e00000000000"
            # # 加入发送队列
            # self._enqueue_packet(use_item_packet_hex)
            # time.sleep(3)
            # use_item_packet_hex = "2d000000e8030100fa0700000000120800001b00000001001500e4b88de59ca8e5908ce4b880e59cb0e59bbe2e2e2e0000"
            # # 加入发送队列
            # self._enqueue_packet(use_item_packet_hex)

    def _update_backpack_display(self):
        try:
            # 清除旧的单选按钮
            for widget in self.gift_frame.winfo_children():
                widget.destroy()

            # 简单的水平排列方法
            self.gift_type_var = tk.StringVar(value="")

            # 使用grid布局实现多列显示
            items_list = list(self.backpack_items.items())
            columns = 3  # 每行3个按钮

            for i, (item_id, item) in enumerate(items_list):
                radio = ttk.Radiobutton(
                    self.gift_frame,
                    text=f"{item.name} x {item.quantity}",
                    variable=self.gift_type_var,
                    value=item_id
                )
                # 使用grid布局，计算行列位置
                row = i // columns
                col = i % columns
                radio.grid(row=row, column=col, padx=5, pady=2, sticky=tk.W)
            # 配置列权重使布局更美观
            for i in range(columns):
                self.gift_frame.columnconfigure(i, weight=1)
        except Exception as e:
            print(f"更新背包显示内容失败: {str(e)}")

    def _trigger_reconnect(self):
        """触发自动重连 - 完全后台执行"""
        # 尝试从配置文件加载登录信息
        self._load_config()

        # 检查是否有保存的账号信息
        if not self.saved_account:
            self.show_warning("重连失败", "未保存登录信息，无法重连！")
            return

        print("开始后台自动重连流程...")
        if hasattr(self, 'reconnect_status_label'):
            self.reconnect_status_label.config(text="正在重连...", foreground="blue")

        # 直接在后台执行完整重连流程
        self._full_background_reconnect()

    def _full_background_reconnect(self):
        """完整的后台重连流程 - 完全模拟手动登录流程"""
        try:
            account = self.saved_account
            password = self.saved_password
            saved_server_name = self.saved_login_info.get("server", "龙一服")
            role = self.saved_login_info.get("role")

            if not account or not password:
                self.show_warning("重连失败", "登录信息不完整")
                return

            print(f"后台重连开始：账号={account}, 服务器={saved_server_name}")

            if hasattr(self, 'reconnect_status_label'):
                self.reconnect_status_label.config(text="正在自动登录...", foreground="blue")

            # 使用后台线程执行重连
            def reconnect_thread():
                try:
                    # 步骤0: 先停止心跳线程
                    print("步骤0: 停止心跳线程...")
                    if hasattr(self, '_heartbeat_running'):
                        self._heartbeat_running = False

                    # 等待一下让旧socket完全释放
                    time.sleep(1)

                    # 步骤1: 先断开所有现有连接
                    if hasattr(self, 'current_sock') and self.current_sock:
                        try:
                            self.current_sock.close()
                        except:
                            pass
                    if hasattr(self, 'game_socket') and self.game_socket:
                        try:
                            self.game_socket.close()
                        except:
                            pass
                    self.current_sock = None
                    self.game_socket = None
                    self.backpack_items = {}

                    # 步骤2: 新建TCP连接，用账号密码登录
                    print("步骤2: 新建TCP连接，自动登录...")

                    # 获取服务器配置
                    server_config = self.server_options.get(saved_server_name)
                    if not server_config:
                        server_config = self.server_options.get("龙一服")

                    server_ip = server_config["ip"]
                    server_port = int(server_config["port"])

                    # 生成登录包并发送
                    body = generate_login_packet(account, password)
                    packet = binascii.unhexlify(body)

                    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    sock.settimeout(10)
                    sock.connect((server_ip, server_port))
                    sock.send(packet)

                    # 接收登录响应（包含session）
                    response = sock.recv(1024)
                    print(f"登录响应: {response.hex() if response else 'None'}")

                    if not response or len(response) < 8:
                        self.root.after(0, lambda: self._show_reconnect_error("登录失败：无响应"))
                        return

                    # 解析session
                    session_id = get_session_id_hex(response)
                    if not session_id:
                        self.root.after(0, lambda: self._show_reconnect_error("登录失败：无效session"))
                        return

                    print(f"登录成功，session_id={session_id}")
                    self.current_session_id = session_id
                    self.current_account = account

                    # 步骤3: 用session连接游戏服务器获取角色列表
                    print("步骤2: 用session连接游戏服务器获取角色列表...")
                    self.root.after(0, lambda: hasattr(self,
                                                       'reconnect_status_label') and self.reconnect_status_label.config(
                        text="正在获取角色...", foreground="blue") or None)

                    # 获取游戏服务器的实际IP和端口（不是登录服务器的IP）
                    # 优先使用保存的服务器信息
                    if hasattr(self, 'current_server_ip') and self.current_server_ip:
                        game_server_ip = self.current_server_ip
                        game_server_port = self.current_server_port
                    else:
                        # 从游戏服务器配置获取实际游戏服务器地址
                        game_config = self.game_server_options.get(saved_server_name)
                        if game_config:
                            game_server_ip = game_config["ip"]
                            game_server_port = game_config["port"]
                        else:
                            # 默认龙二服
                            game_server_ip = "tl10.shuihl.cn"
                            game_server_port = 12001

                    print(f"游戏服务器地址: {game_server_ip}:{game_server_port}")

                    # 关闭登录socket，创建新的socket连接游戏服务器
                    try:
                        sock.close()
                    except:
                        pass

                    # 连接游戏服务器获取角色列表（创建新socket，使用游戏服务器地址）
                    role_list, game_sock = connect_game_server_get_roles(
                        session_id,
                        game_server_ip,
                        game_server_port,
                        None  # 使用新socket，不复用登录socket
                    )

                    # 如果获取角色列表失败（0字节响应），尝试重新登录一次
                    if not role_list or 'error' in role_list or (role_list.get('userList') is None):
                        print(f"获取角色列表失败，尝试重新连接...")
                        if game_sock:
                            try:
                                game_sock.close()
                            except:
                                pass

                        # 等待一下再重试
                        time.sleep(2)

                        # 重新创建socket并获取角色列表
                        game_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                        game_sock.settimeout(15)
                        game_sock.connect((game_server_ip, game_server_port))

                        # 重新发送角色列表请求
                        body = '1a000000e8030200eb031902f605f003000008000000' + session_id
                        packet = binascii.unhexlify(body)
                        game_sock.send(packet)

                        # 接收响应
                        response = game_sock.recv(10240)
                        print(f"重试收到响应，长度: {len(response) if response else 0} 字节")

                        if response and len(response) > 0:
                            role_list = parse_role_data(response.hex())
                            role_list['sessionId'] = session_id
                            print(f"重试获取角色列表成功: {role_list}")
                        else:
                            role_list = {'userList': []}

                    if not game_sock:
                        self.root.after(0, lambda: self._show_reconnect_error("连接游戏服务器失败"))
                        return

                    self.current_sock = game_sock

                    # 检查角色列表
                    if not role_list or 'userList' not in role_list or len(role_list['userList']) == 0:
                        self.root.after(0, lambda: self._show_reconnect_error("获取角色列表失败"))
                        return

                    self.current_role_list = role_list['userList']
                    print(f"获取到 {len(self.current_role_list)} 个角色")

                    # 步骤4: 自动选择角色
                    print("步骤3: 自动选择角色...")
                    selected_role = None

                    if role:
                        saved_role_id = role.get("role_id")
                        # 查找匹配的角色
                        for r in role_list['userList']:
                            if r.get("role_id") == saved_role_id:
                                selected_role = r
                                break

                    # 没找到就用第一个
                    if not selected_role:
                        selected_role = role_list['userList'][0]

                    role_id = selected_role.get("role_id")
                    role_name = selected_role.get("role_name_cn")

                    print(f"自动选择角色: {role_name} (ID: {role_id})")
                    self.root.after(0, lambda: hasattr(self,
                                                       'reconnect_status_label') and self.reconnect_status_label.config(
                        text=f"进入 {role_name}...", foreground="blue") or None)

                    # 保存当前角色
                    self.current_role = selected_role

                    # 步骤5: 发送选角请求进入游戏
                    print("步骤4: 发送选角请求...")
                    # 使用正确的选角请求格式
                    body = '18000000e8030200ee03a3fbf505f103000006000000485302000000'
                    # 替换角色ID
                    body = body.replace("485302", role_id)
                    packet = binascii.unhexlify(body)
                    game_sock.send(packet)

                    # 接收响应
                    game_sock.settimeout(15)
                    resp = game_sock.recv(4048)
                    print(f"选角响应: {resp.hex() if resp else 'None'}")

                    # 处理选角响应报文
                    if resp:
                        print("正在处理选角响应报文...")
                        self._on_receive_packet(resp)

                    # 步骤6: 进入游戏主页
                    print("步骤5: 进入游戏主页...")
                    self.root.after(0, self._refresh_game_main_frame)

                except Exception as e:
                    print(f"后台重连线程失败: {str(e)}")
                    import traceback
                    traceback.print_exc()
                    error_msg = str(e)
                    self.root.after(0, lambda msg=error_msg: self._show_reconnect_error(msg))

            thread = threading.Thread(target=reconnect_thread)
            thread.daemon = True
            thread.start()

        except Exception as e:
            print(f"后台重连失败: {str(e)}")
            self.show_warning("重连失败", f"重连出错: {str(e)}")

    def _show_reconnect_error(self, error_msg):
        """显示重连错误"""
        if hasattr(self, 'reconnect_status_label'):
            self.reconnect_status_label.config(text="重连失败", foreground="red")
        self.show_warning("重连失败", error_msg)

    def _refresh_game_main_frame(self):
        """刷新游戏主界面"""
        print("刷新游戏主界面...")
        if hasattr(self, 'reconnect_status_label'):
            self.reconnect_status_label.config(text="重连成功！", foreground="green")

        # 设置game_socket（重连后使用新的socket）
        if hasattr(self, 'current_sock') and self.current_sock:
            self.game_socket = self.current_sock
            # 设置为1秒超时，便于后续接收循环
            self.game_socket.settimeout(5)
            print(f"成功设置套接字超时为1秒，准备持续接收报文")

            # 启动接收报文的线程
            receive_thread = start_receive_thread(
                self.game_socket,
                self._on_receive_packet  # 回调函数
            )

            # 调用连接成功回调
            self._on_server_connect_success(self.game_socket, receive_thread)

        # 重新启动心跳线程
        self._start_heartbeat_thread()

        # 刷新界面数据（重新接收报文数据）
        print("重连完成，游戏数据已刷新")

    def _request_refresh_game_data(self):
        """请求刷新游戏数据（角色信息、背包等）"""
        print("请求刷新游戏数据...")
        # 清空现有数据
        if hasattr(self, 'backpack_items'):
            self.backpack_items.clear()

        # 更新背包显示
        if hasattr(self, '_update_backpack_display'):
            self._update_backpack_display()

        print("已发送刷新请求，等待服务器返回数据...")

    def _refresh_backpack_manual(self):
        """手动刷新背包"""
        print("手动刷新背包...")
        if hasattr(self, '_update_backpack_display'):
            self._update_backpack_display()
        print(f"背包当前物品数量: {len(self.backpack_items)}")

    def _delayed_refresh_backpack(self):
        """延迟刷新背包"""
        print("延迟刷新背包...")
        if hasattr(self, '_update_backpack_display'):
            self._update_backpack_display()
        print(f"背包当前物品数量: {len(self.backpack_items)}")

    def _do_background_reconnect(self):
        """后台执行重连 - 不显示任何中间界面"""
        try:
            account = self.saved_account
            password = self.saved_password

            if not account or not password:
                self.show_warning("重连失败", "登录信息不完整")
                return

            print(f"后台重连：账号={account}")

            # 步骤1：登录获取session
            # 直接调用本类的login方法
            self.login()

            if session_id:
                print(f"登录成功，session_id={session_id}")
                self.current_session_id = session_id
                self.current_account = account

                # 步骤2：保存登录信息
                self.saved_login_info["account"] = account
                self.saved_login_info["password"] = password

                if hasattr(self, 'reconnect_status_label'):
                    self.reconnect_status_label.config(text="登录成功，正在连接服务器...", foreground="blue")

                # 步骤3：自动连接服务器并获取角色列表
                self.root.after(100, lambda: self._auto_connect_and_enter(account, session_id, response))
            else:
                if hasattr(self, 'reconnect_status_label'):
                    self.reconnect_status_label.config(text="登录失败", foreground="red")
                self.show_warning("重连失败", "登录验证失败")

        except Exception as e:
            print(f"后台重连失败: {str(e)}")
            if hasattr(self, 'reconnect_status_label'):
                self.reconnect_status_label.config(text="重连失败", foreground="red")
            self.show_warning("重连失败", f"重连出错: {str(e)}")

    def _auto_connect_and_enter(self, account, session_id, response):
        """自动连接服务器、选择角色、进入游戏"""
        try:
            # 从当前服务器选择连接
            # 使用当前界面选择的服务器
            server_name = self.current_server if hasattr(self, 'current_server') and self.current_server else "生死符"
            server_ip = self.current_server_ip if hasattr(self, 'current_server_ip') else "tl10.shuihl.cn"
            server_port = self.current_server_port if hasattr(self, 'current_server_port') else 12001

            print(f"连接服务器: {server_name} ({server_ip}:{server_port})")

            if hasattr(self, 'reconnect_status_label'):
                self.reconnect_status_label.config(text=f"正在连接 {server_name}...", foreground="blue")

            # 连接游戏服务器
            self._connect_to_game_server(server_ip, server_port)

            if hasattr(self, 'reconnect_status_label'):
                self.reconnect_status_label.config(text="正在选择角色...", foreground="blue")

            # 发送选角请求
            self.root.after(500, lambda: self._send_select_role_request(account, session_id))

        except Exception as e:
            print(f"自动连接服务器失败: {str(e)}")
            if hasattr(self, 'reconnect_status_label'):
                self.reconnect_status_label.config(text="连接失败", foreground="red")
            self.show_warning("重连失败", f"连接服务器失败: {str(e)}")

    def _send_select_role_request(self, account, session_id):
        """发送选角请求"""
        try:
            # 使用保存的角色信息
            saved_role = self.saved_login_info.get("role")
            if saved_role:
                role_id = saved_role.get("role_id")
                role_name = saved_role.get("role_name_cn")
                print(f"自动选择角色: {role_name} (ID: {role_id})")

                if hasattr(self, 'reconnect_status_label'):
                    self.reconnect_status_label.config(text=f"进入 {role_name}...", foreground="blue")

                # 发送选角协议
                self._send_select_role_packet(role_id)

                # 进入游戏
                self.root.after(500, self._enter_game_main_frame)
            else:
                # 没有保存的角色，需要重新获取角色列表
                print("没有保存的角色信息，需要重新获取")
                # 发送获取角色列表请求
                self._send_get_role_list_packet()
                self.root.after(1000, self._auto_enter_first_role)

        except Exception as e:
            print(f"发送选角请求失败: {str(e)}")
            if hasattr(self, 'reconnect_status_label'):
                self.reconnect_status_label.config(text="选角失败", foreground="red")
            self.show_warning("重连失败", f"选角失败: {str(e)}")

    def _auto_enter_first_role(self):
        """自动进入第一个角色"""
        try:
            if hasattr(self, 'current_role_list') and self.current_role_list:
                role = self.current_role_list[0]
                role_id = role.get("role_id")
                print(f"自动选择第一个角色: {role.get('role_name_cn')}")
                self._send_select_role_packet(role_id)
                self.root.after(500, self._enter_game_main_frame)
            else:
                self.show_warning("重连失败", "无法获取角色列表")
        except Exception as e:
            print(f"自动进入角色失败: {str(e)}")

    def _send_select_role_packet(self, role_id):
        """发送选择角色请求"""
        try:
            if hasattr(self, 'current_sock') and self.current_sock:
                print(f"发送选择角色请求，role_id={role_id}...")

                # 使用选择角色请求格式
                body = '18000000e8030200ee03a3fbf505f103000006000000485302'
                # 替换角色ID（最后6位）
                role_id_hex = role_id[-6:]  # 取最后6位
                body = body + role_id_hex

                packet = binascii.unhexlify(body)
                self.current_sock.send(packet)
                print(f"发送选择角色请求成功")

                # 接收响应
                self.current_sock.settimeout(5)
                response = self.current_sock.recv(4048)
                print(f"收到选择角色响应: {response.hex() if response else 'None'}")

        except Exception as e:
            print(f"发送选择角色请求失败: {str(e)}")

    def _send_get_role_list_packet(self):
        """发送获取角色列表请求"""
        try:
            if hasattr(self, 'current_sock') and self.current_sock:
                print("发送获取角色列表请求...")

                # 使用获取角色列表请求格式
                body = '10000000e8030200ee03a3fbf505f103000006000000'

                packet = binascii.unhexlify(body)
                self.current_sock.send(packet)
                print("发送获取角色列表请求成功")

                # 接收响应
                self.current_sock.settimeout(5)
                response = self.current_sock.recv(4048)
                print(f"收到角色列表响应: {response.hex() if response else 'None'}")

        except Exception as e:
            print(f"发送获取角色列表请求失败: {str(e)}")

    def _connect_to_game_server(self, server_ip, server_port):
        """连接游戏服务器"""
        try:
            import socket
            print(f"连接游戏服务器: {server_ip}:{server_port}")

            # 创建socket连接
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.connect((server_ip, server_port))
            sock.settimeout(10)

            # 保存socket对象
            self.current_sock = sock
            print("游戏服务器连接成功")

            return True

        except Exception as e:
            print(f"连接游戏服务器失败: {str(e)}")
            return False

    def _enter_game_main_frame(self):
        """直接进入游戏主界面"""
        print("进入游戏主界面...")
        if hasattr(self, 'reconnect_status_label'):
            self.reconnect_status_label.config(text="重连成功！", foreground="green")
        # 显示游戏主界面
        self._on_enter_game()

    def _auto_login(self):
        """自动重连：登录账号 → 选择服务器 → 选择角色 → 进入游戏"""
        try:
            # 步骤1：自动登录账号（从配置文件读取）
            account = self.saved_account
            password = self.saved_password
            server = self.server_var.get()  # 使用当前选择的服务器

            if not account or not password:
                self.show_warning("重连失败", "登录信息不完整")
                return

            print(f"自动重连：账号={account}, 服务器={server}")

            # 设置登录信息
            self.account_var.set(account)
            self.password_var.set(password)
            # server_var 保持不变，因为已经在服务器选择页面

            # 调用登录方法
            self.login()

        except Exception as e:
            print(f"自动重连失败: {str(e)}")
            self.show_warning("重连失败", f"自动重连出错: {str(e)}")

    def _auto_select_server(self):
        """自动选择服务器"""
        try:
            # 获取之前选择的服务器名称
            saved_server = self.saved_login_info.get("server")
            if not saved_server:
                print("没有保存的服务器信息")
                return

            # 查找匹配的服务器
            for item in self.server_tree.get_children():
                server_name = self.server_tree.item(item, 'values')[0]
                if saved_server in server_name or server_name in saved_server:
                    print(f"自动选择服务器: {server_name}")
                    self.server_tree.selection_set(item)
                    self.server_tree.see(item)
                    # 触发服务器选择
                    self.root.after(300, self._auto_connect_server)
                    return

            print(f"未找到匹配的服务器: {saved_server}")

        except Exception as e:
            print(f"自动选择服务器失败: {str(e)}")

    def _auto_connect_server(self):
        """自动连接服务器并选择角色"""
        try:
            # 触发服务器选择
            self._on_select_server()
            print("已触发服务器选择，等待获取角色列表...")

        except Exception as e:
            print(f"自动连接服务器失败: {str(e)}")

    def _auto_select_character(self):
        """自动选择角色并进入游戏"""
        try:
            # 获取保存的角色信息
            saved_role = self.saved_login_info.get("role")
            if not saved_role:
                print("没有保存的角色信息")
                return

            saved_role_id = saved_role.get("role_id")

            # 查找匹配的角色
            if self.current_role_list:
                for role in self.current_role_list:
                    if role.get("role_id") == saved_role_id:
                        print(f"自动选择角色: {role.get('role_name_cn')}")
                        # 选择角色
                        self._select_character(role)
                        # 自动进入游戏
                        self.root.after(300, self._on_enter_game)
                        return

            # 如果没找到匹配的角色，默认选择第一个
            if self.current_role_list and len(self.current_role_list) > 0:
                role = self.current_role_list[0]
                print(f"未找到匹配角色，默认选择: {role.get('role_name_cn')}")
                self._select_character(role)
                self.root.after(300, self._on_enter_game)

        except Exception as e:
            print(f"自动选择角色失败: {str(e)}")


def main():
    root = tk.Tk()
    LoginApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
