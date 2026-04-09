"""
统一 random_num 生成逻辑。
供 battle、item_use 等脚本复用。
"""

import random


def random_num_hex4() -> str:
    """返回 4 位小写 hex。"""
    return format(random.randint(0x0000, 0xFFFF), "04x")


def random_num_hex6() -> str:
    """返回 6 位小写 hex。"""
    return format(random.randint(0x100000, 0xFFFFFF), "06x")
