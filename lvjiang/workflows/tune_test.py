"""单次调律测试工作流 - 场景联调"""

import random
import time

from loguru import logger

from .base import WorkflowBase


# 该工作流涉及的全部场景
REQUIRED_SCENES = [
    "game_main_page", "game_menu_page", "equip_bag_detail",
    "equip_weapon_detail", "equip_tune_detail", "equip_tune_result",
]


class TuneTestWorkflow(WorkflowBase):
    """单次调律测试工作流"""

    def run(self) -> str:
        """
        执行单次调律测试流程
        返回: 调律词条 OCR 文本
        """
        logger.info("=== 单次调律测试流程开始 ===")

        def _wait():
            """步骤间等待（从配置读取 step_interval）"""
            time.sleep(random.uniform(*self._delay.step_interval))
            return not self._stop_check()

        # 1. 游戏主页 → 点击菜单 → 游戏菜单
        self._click_region("game_main_page", "menu")
        if not _wait(): return "(已停止)"

        # 2. 游戏菜单 → 点击包裹 → 背包详情
        self._click_region("game_menu_page", "bag")
        if not _wait(): return "(已停止)"

        # 3. 背包详情 → 点击主武器slot → 锁定武器类型
        self._click_region("equip_bag_detail", "slot_main_weapon")
        if not _wait(): return "(已停止)"

        # 4. 背包详情 → 点击背包格_1_2 → 武器详情
        self._click_region("equip_bag_detail", "bag_1_2")
        if not _wait(): return "(已停止)"

        # 5. 武器详情 → 点击更多功能 → 展开子功能
        self._click_region("equip_weapon_detail", "more_func")
        if not _wait(): return "(已停止)"

        # 6. 武器详情 → OCR 四个次要功能按钮，找到"调律"并点击
        sub_func_keys = ["sub_func_1", "sub_func_2", "sub_func_3", "sub_func_4"]
        ocr_results = self._ocr_scene("equip_weapon_detail")
        tune_key = None
        for key in sub_func_keys:
            text = ocr_results.get(key, "")
            logger.debug(f"  {key}: {text}")
            if "调律" in text:
                tune_key = key
                break

        if tune_key is None:
            logger.error(f'四个次要功能中未找到「调律」，OCR 结果: {ocr_results}')
            return "(错误: 未找到调律按钮)"

        logger.info(f"找到调律按钮: {tune_key}")
        self._click_region("equip_weapon_detail", tune_key)
        if not _wait(): return "(已停止)"

        # 7. 调律详情 → 点击一键添加
        self._click_region("equip_tune_detail", "one_click_add")
        if not _wait(): return "(已停止)"

        # 8. 调律详情 → 点击调律
        self._click_region("equip_tune_detail", "tune_btn")
        if not _wait(): return "(已停止)"

        # 9. 等待调律动画后截图 OCR 调律结果（从配置读取 after_tune_wait）
        logger.info(f"等待 {self._delay.after_tune_wait} 秒后截图分析调律结果...")
        time.sleep(self._delay.after_tune_wait)
        tune_result = self._ocr_scene("equip_tune_result")
        tune_affix = tune_result.get("tune_affix", "(未识别到)")
        logger.info(f"调律词条: {tune_affix}")

        # 10. 调律结果 → 点击关闭 → 回到调律详情
        self._click_region("equip_tune_result", "close_btn")
        if not _wait(): return "(已停止)"

        # 11. 调律详情 → 点击返回 → 背包详情
        self._click_region("equip_tune_detail", "back")
        if not _wait(): return "(已停止)"

        # 12. 背包详情 → 点击3次返回 → 游戏菜单
        # 从次要功能退出来必须先点击一次退出次要功能，再点击一次退出装备详情
        self._click_region("equip_bag_detail", "back")
        if not _wait(): return "(已停止)"

        self._click_region("equip_bag_detail", "back")
        if not _wait(): return "(已停止)"

        self._click_region("equip_bag_detail", "back")
        if not _wait(): return "(已停止)"

        # 13. 游戏菜单 → 点击返回 → 游戏主页
        self._click_region("game_menu_page", "back")
        time.sleep(random.uniform(*self._delay.step_interval))

        logger.info("=== 单次调律测试流程完成 ===")
        return tune_affix
