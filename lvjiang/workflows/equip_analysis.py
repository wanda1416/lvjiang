"""装备分析工作流 - 基于流程 01: 用户当前装备分析"""

import json
import time

from loguru import logger

from .base import WorkflowBase
from ..constants import LOCAL_CONFIG_DIR, EQUIP_SLOT_NAMES


# 8 个目标槽位（按优先级排序）：(场景字段 key, 输出 key)
TARGET_SLOTS = [
    ("slot_main_weapon", "main_weapon"),
    ("slot_sub_weapon",  "sub_weapon"),
    ("slot_ring",        "ring"),
    ("slot_pendant",     "pendant"),
    ("slot_head",        "head"),
    ("slot_chest",       "chest"),
    ("slot_leg",         "leg"),
    ("slot_wrist",       "wrist"),
]

# 前 4 个是武器类，后 4 个是防具类
WEAPON_SLOTS = {s[0] for s in TARGET_SLOTS[:4]}
ARMOR_SLOTS = {s[0] for s in TARGET_SLOTS[4:]}

# 装备分析所需场景
REQUIRED_SCENES = ["equip_bag_detail", "equip_weapon_detail", "equip_armor_detail"]


class EquipAnalysisWorkflow(WorkflowBase):
    """装备分析工作流"""

    def __init__(self, user_name: str, **kwargs):
        super().__init__(**kwargs)
        self._user_name = user_name

    def run(self) -> dict:
        """
        执行装备分析流程
        返回: dict，key 为槽位英文名，value 为字段 dict
        """
        logger.info("=== 装备分析流程开始 ===")
        result = {}

        # 获取背包场景的区域定义
        bag_regions = self._layout.get_scene_regions("equip_bag_detail")
        if not bag_regions:
            logger.error("背包场景没有定义区域")
            return result

        # 构建 key -> Region 映射
        bag_map = {r.key: r for r in bag_regions}

        for slot_key, output_key in TARGET_SLOTS:
            if self._stop_check():
                logger.info("装备分析流程被用户停止")
                break

            slot_name = EQUIP_SLOT_NAMES.get(output_key, output_key)
            if slot_key not in bag_map:
                logger.warning(f"槽位 {slot_name} 没有定义区域，跳过")
                continue

            logger.info(f"--- 处理槽位: {slot_name} ---")

            try:
                # 1. 点击槽位（背包页和详情页同图层，直接切换）
                self._click_region("equip_bag_detail", slot_key)
                time.sleep(self._delay.page_refresh_wait)

                # 2. 截图 + OCR
                if slot_key in WEAPON_SLOTS:
                    equip_data = self._ocr_scene("equip_weapon_detail")
                else:
                    equip_data = self._ocr_scene("equip_armor_detail")

                result[output_key] = equip_data
                logger.info(f"  识别结果: {equip_data}")
            except Exception as e:
                logger.error(f"  槽位 {slot_name} 处理失败: {e}")
                import traceback
                logger.error(traceback.format_exc())
                continue

        # 4. 保存结果
        self._save_result(result)
        logger.info(f"=== 装备分析流程完成，共 {len(result)} 件装备 ===")
        return result

    def _save_result(self, result: dict):
        """保存结果到 config/user/users/{username}/equipments.json"""
        user_dir = LOCAL_CONFIG_DIR / "users" / self._user_name
        user_dir.mkdir(parents=True, exist_ok=True)
        output_path = user_dir / "equipments.json"

        output_path.write_text(
            json.dumps(result, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info(f"装备数据已保存: {output_path}")
