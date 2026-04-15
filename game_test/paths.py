"""
数据目录解析。

- 未设置 LWCS_INSTANCE_SLUG（直接 python run.py）：扁平模式，全部读写 game_test/data/ 根目录
  （monsters、fingerprints、quick_logins、packet_logs 等与旧版一致）。
- 已设置 LWCS_INSTANCE_SLUG（多开子进程）：分目录模式，共用 data/shared/，实例 data/accounts/<slug>/。
"""

from __future__ import annotations

import os

_GAME_TEST_DIR = os.path.dirname(os.path.abspath(__file__))

_SLUG_ENV = (os.environ.get("LWCS_INSTANCE_SLUG") or "").strip()


def _resolved_data_root() -> str:
    raw = (os.environ.get("LWCS_DATA_ROOT") or "").strip()
    if raw:
        return os.path.normpath(os.path.abspath(raw))
    return os.path.normpath(os.path.join(_GAME_TEST_DIR, "data"))


DATA_ROOT = _resolved_data_root()

if _SLUG_ENV:
    INSTANCE_SLUG = _SLUG_ENV
    SHARED_DATA_DIR = os.path.join(DATA_ROOT, "shared")
    INSTANCE_DATA_DIR = os.path.join(DATA_ROOT, "accounts", INSTANCE_SLUG)
else:
    INSTANCE_SLUG = ""
    SHARED_DATA_DIR = DATA_ROOT
    INSTANCE_DATA_DIR = DATA_ROOT

MONSTERS_FILE = os.path.join(SHARED_DATA_DIR, "monsters.json")
FINGERPRINTS_FILE = os.path.join(SHARED_DATA_DIR, "fingerprints.json")
TELEPORT_DESTINATIONS_FILE = os.path.join(SHARED_DATA_DIR, "teleport_destination.json")

QUICK_LOGINS_FILE = os.path.join(INSTANCE_DATA_DIR, "quick_logins.json")
BUY_ITEMS_FILE = os.path.join(INSTANCE_DATA_DIR, "buy_items.json")
LIAOGUO_PAIRS_FILE = os.path.join(INSTANCE_DATA_DIR, "liaoguo_pairs.json")
AUTO_USE_RULES_FILE = os.path.join(INSTANCE_DATA_DIR, "auto_use_rules.json")
ANNOTATIONS_FILE = os.path.join(INSTANCE_DATA_DIR, "annotations.json")
PACKET_LOG_DIR = os.path.join(INSTANCE_DATA_DIR, "packet_logs")


def ensure_data_directories() -> None:
    """创建数据目录；扁平模式仅保证 data/ 与 packet_logs；分目录模式再建 shared、accounts/<slug>。"""
    os.makedirs(DATA_ROOT, exist_ok=True)
    if _SLUG_ENV:
        os.makedirs(SHARED_DATA_DIR, exist_ok=True)
        os.makedirs(INSTANCE_DATA_DIR, exist_ok=True)
        os.makedirs(PACKET_LOG_DIR, exist_ok=True)
    else:
        os.makedirs(PACKET_LOG_DIR, exist_ok=True)
