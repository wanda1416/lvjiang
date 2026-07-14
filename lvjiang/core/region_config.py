"""POI 区域配置 - 相对比例坐标 + JSON 持久化"""

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path

from loguru import logger

from ..constants import CONFIG_DIR

# 预设字段定义（固定 8 个字段）
EQUIP_FIELDS: list[tuple[str, str]] = [
    ("equip_type",  "装备类型"),
    ("equip_level", "装备等级"),
    ("base_attr",   "基础属性"),
    ("affix_1",     "词条1"),
    ("affix_2",     "词条2"),
    ("affix_3",     "词条3"),
    ("affix_4",     "词条4"),
    ("affix_5",     "词条5"),
]

REGIONS_DIR = CONFIG_DIR / "regions"


@dataclass
class Region:
    """单个区域定义（相对比例坐标）"""
    key: str               # 字段标识，如 "equip_type"
    name: str              # 显示名称，如 "装备类型"
    x_ratio: float         # 左上角 X 比例 (0.0~1.0)
    y_ratio: float         # 左上角 Y 比例 (0.0~1.0)
    w_ratio: float         # 宽度比例 (0.0~1.0)
    h_ratio: float         # 高度比例 (0.0~1.0)

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "Region":
        return Region(**d)


@dataclass
class RegionPreset:
    """一套区域预设"""
    name: str = "默认布局"
    regions: list[Region] = field(default_factory=list)

    def get_region(self, key: str) -> Region | None:
        for r in self.regions:
            if r.key == key:
                return r
        return None

    def assigned_keys(self) -> set[str]:
        return {r.key for r in self.regions}

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "regions": [r.to_dict() for r in self.regions],
        }

    @staticmethod
    def from_dict(d: dict) -> "RegionPreset":
        return RegionPreset(
            name=d.get("name", "默认布局"),
            regions=[Region.from_dict(r) for r in d.get("regions", [])],
        )


class RegionConfigManager:
    """管理多套区域预设的加载/保存"""

    def __init__(self):
        REGIONS_DIR.mkdir(parents=True, exist_ok=True)

    def _preset_path(self, name: str) -> Path:
        # 文件名用预设名，去掉不安全字符
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in name)
        return REGIONS_DIR / f"{safe}.json"

    def list_presets(self) -> list[str]:
        """列出所有已保存的预设名称"""
        names = []
        for p in sorted(REGIONS_DIR.glob("*.json")):
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                names.append(data.get("name", p.stem))
            except Exception:
                names.append(p.stem)
        return names

    def save_preset(self, preset: RegionPreset) -> Path:
        """保存预设到 JSON 文件"""
        path = self._preset_path(preset.name)
        path.write_text(
            json.dumps(preset.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info(f"区域预设已保存: {path}")
        return path

    def load_preset(self, name: str) -> RegionPreset | None:
        """加载指定名称的预设"""
        path = self._preset_path(name)
        if not path.exists():
            logger.warning(f"区域预设不存在: {path}")
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            preset = RegionPreset.from_dict(data)
            logger.info(f"区域预设已加载: {preset.name} ({len(preset.regions)} 个区域)")
            return preset
        except Exception as e:
            logger.error(f"加载区域预设失败: {e}")
            return None

    def delete_preset(self, name: str) -> bool:
        """删除指定预设"""
        path = self._preset_path(name)
        if path.exists():
            path.unlink()
            logger.info(f"区域预设已删除: {path}")
            return True
        return False

