"""装备分析工作流 - 基于流程 01: 用户当前装备分析"""

import json
import random
import time

import pyautogui
from loguru import logger

from ..core.capture import ScreenCapture
from ..core.ocr import OCREngine
from ..core.input import InputController
from ..core.region_config import (
    Layout, Region, get_scene_fields, FIELD_GROUPS,
)
from ..constants import USER_CONFIG_DIR


# 8 个目标槽位（按优先级排序）
TARGET_SLOTS = [
    ("slot_main_weapon", "main_weapon", "主武器"),
    ("slot_sub_weapon",  "sub_weapon",  "副武器"),
    ("slot_ring",        "ring",        "环"),
    ("slot_pendant",     "pendant",     "佩"),
    ("slot_head",        "head",        "冠胄"),
    ("slot_chest",       "chest",       "胸甲"),
    ("slot_leg",         "leg",         "胫甲"),
    ("slot_wrist",       "wrist",       "腕甲"),
]

# 前 4 个是武器类，后 4 个是防具类
WEAPON_SLOTS = {s[0] for s in TARGET_SLOTS[:4]}
ARMOR_SLOTS = {s[0] for s in TARGET_SLOTS[4:]}

# 点击后等待页面刷新时间（秒）
CLICK_WAIT = 2.0


class EquipAnalysisWorkflow:
    """装备分析工作流"""

    def __init__(
        self,
        capture: ScreenCapture,
        ocr: OCREngine,
        input_ctrl: InputController,
        layout: Layout,
        user_name: str,
        window_left: int = 0,
        window_top: int = 0,
    ):
        self._capture = capture
        self._ocr = ocr
        self._input = input_ctrl
        self._layout = layout
        self._user_name = user_name
        self._window_left = window_left  # 窗口在屏幕上的左坐标
        self._window_top = window_top    # 窗口在屏幕上的上坐标

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

        for slot_key, output_key, slot_name in TARGET_SLOTS:
            if slot_key not in bag_map:
                logger.warning(f"槽位 {slot_name} 没有定义区域，跳过")
                continue

            region = bag_map[slot_key]
            logger.info(f"--- 处理槽位: {slot_name} ---")

            # 1. 点击槽位（背包页和详情页同图层，直接切换）
            self._click_slot(region)
            time.sleep(CLICK_WAIT)

            # 2. 截图 + OCR
            if slot_key in WEAPON_SLOTS:
                equip_data = self._capture_and_ocr("equip_weapon_detail")
            else:
                equip_data = self._capture_and_ocr("equip_armor_detail")

            result[output_key] = equip_data
            logger.info(f"  识别结果: {equip_data}")

        # 4. 保存结果
        self._save_result(result)
        logger.info(f"=== 装备分析流程完成，共 {len(result)} 件装备 ===")
        return result

    def _click_slot(self, region: Region):
        """点击槽位区域中心 0.25~0.75 范围内的随机坐标"""
        # 获取截图尺寸
        img = self._capture.capture()
        if img is None:
            logger.error("截图失败，无法计算点击坐标")
            return

        h, w = img.shape[:2]
        canvas = self._layout.get_canvas()

        # 画布变换：区域坐标 -> 截图像素
        canvas_x = canvas.x_ratio * w
        canvas_y = canvas.y_ratio * h
        canvas_w = canvas.w_ratio * w
        canvas_h = canvas.h_ratio * h

        # 区域在截图中的像素位置
        region_cx = canvas_x + (region.x_ratio + region.w_ratio / 2) * canvas_w
        region_cy = canvas_y + (region.y_ratio + region.h_ratio / 2) * canvas_h
        region_pw = region.w_ratio * canvas_w
        region_ph = region.h_ratio * canvas_h

        # 在中心 0.25~0.75 范围内随机取点
        rx = region_cx + region_pw * (random.uniform(-0.25, 0.25))
        ry = region_cy + region_ph * (random.uniform(-0.25, 0.25))

        # 转换为屏幕坐标（加上窗口偏移）
        screen_x = int(self._window_left + rx)
        screen_y = int(self._window_top + ry)

        logger.debug(f"点击槽位: 截图坐标({rx:.0f},{ry:.0f}) -> 屏幕坐标({screen_x},{screen_y})")
        # 缓慢移动鼠标到目标位置（0.3-0.6秒），模拟人类操作
        pyautogui.moveTo(screen_x, screen_y, duration=random.uniform(0.3, 0.6))
        # 点击前短暂延迟
        time.sleep(random.uniform(0.05, 0.15))
        pyautogui.click(screen_x, screen_y)

    def _capture_and_ocr(self, scene_key: str) -> dict:
        """
        截取当前画面，对指定场景的所有区域进行 OCR
        返回: dict，key 为字段 key，value 为 OCR 文本
        """
        img = self._capture.capture()
        if img is None:
            logger.error("截图失败")
            return {}

        h, w = img.shape[:2]
        canvas = self._layout.get_canvas()

        # 画布变换
        canvas_x = canvas.x_ratio * w
        canvas_y = canvas.y_ratio * h
        canvas_w = canvas.w_ratio * w
        canvas_h = canvas.h_ratio * h

        regions = self._layout.get_scene_regions(scene_key)
        if not regions:
            logger.warning(f"场景 {scene_key} 没有定义区域")
            return {}

        fields = get_scene_fields(scene_key)
        field_map = {k: name for k, name in fields}
        result = {}

        for region in regions:
            if region.key not in field_map:
                continue

            # 区域坐标 -> 截图像素
            x1 = int(canvas_x + region.x_ratio * canvas_w)
            y1 = int(canvas_y + region.y_ratio * canvas_h)
            x2 = int(canvas_x + (region.x_ratio + region.w_ratio) * canvas_w)
            y2 = int(canvas_y + (region.y_ratio + region.h_ratio) * canvas_h)

            # 裁剪 + OCR
            crop = img[y1:y2, x1:x2]
            ocr_results = self._ocr.recognize(crop)
            text = " | ".join(r.text for r in ocr_results) if ocr_results else ""
            result[region.key] = text

        return result

    def _save_result(self, result: dict):
        """保存结果到 config/user/users/{username}/equipments.json"""
        user_dir = USER_CONFIG_DIR / "users" / self._user_name
        user_dir.mkdir(parents=True, exist_ok=True)
        output_path = user_dir / "equipments.json"

        output_path.write_text(
            json.dumps(result, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info(f"装备数据已保存: {output_path}")
