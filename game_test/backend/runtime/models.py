"""运行时共享数据模型。"""

from dataclasses import dataclass
from typing import Any, Dict


@dataclass
class Item:
    """背包物品。"""

    item_id: str
    name: str
    quantity: int
    can_disassemble: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "item_id": self.item_id,
            "name": self.name,
            "quantity": self.quantity,
            "can_disassemble": self.can_disassemble,
        }


@dataclass
class RoleInfo:
    """角色信息。"""

    role_id: str
    role_name: str
    role_job: str
    role_index: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "role_id": self.role_id,
            "role_name": self.role_name,
            "role_job": self.role_job,
            "role_index": self.role_index,
        }
