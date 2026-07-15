"""输入控制模块 - 封装 pyautogui 点击，模拟人类操作"""

import random
import time

import pyautogui
from loguru import logger

from ..config import DelayConfig


class InputController:
    """输入控制器（所有点击延迟参数统一从 DelayConfig 读取）
    
    公开接口：仅 click_screen
    """

    def __init__(self, delay_config: DelayConfig | None = None):
        cfg = delay_config or DelayConfig()
        self.before_click_wait = cfg.before_click_wait
        self.after_click_wait = cfg.after_click_wait
        self.mouse_move_duration = cfg.mouse_move_duration
        self.click_random_offset = cfg.click_random_offset
        self.region_jitter_ratio = cfg.region_jitter_ratio
        # pyautogui 安全设置
        pyautogui.FAILSAFE = True  # 鼠标移到左上角可中断
        pyautogui.PAUSE = 0.05

    def click_screen(self, screen_x: int, screen_y: int, poi_name: str = ""):
        """
        点击屏幕坐标（带鼠标移动时长 + 点击后延迟）
        工作流唯一调用的公开方法
        """
        self._move_to(screen_x, screen_y)
        self._click(screen_x, screen_y, poi_name)

    def _move_to(self, x: int, y: int):
        """移动鼠标到指定位置（时长随机化）"""
        duration = random.uniform(*self.mouse_move_duration)
        pyautogui.moveTo(x, y, duration=duration)

    def _click(self, x: int, y: int, poi_name: str = ""):
        """
        点击指定坐标（加入随机偏移和延迟模拟人类）
        poi_name: 可选的 POI 名称，用于日志
        """
        # 随机偏移
        offset_x = random.randint(-self.click_random_offset, self.click_random_offset)
        offset_y = random.randint(-self.click_random_offset, self.click_random_offset)
        actual_x = x + offset_x
        actual_y = y + offset_y

        # 点击前延迟（模拟人类反应时间）
        pre_delay = random.uniform(*self.before_click_wait)
        time.sleep(pre_delay)

        # 执行点击
        label = f"({poi_name})" if poi_name else ""
        logger.debug(f"点击 {label}: ({actual_x}, {actual_y}) [偏移: {offset_x:+d}, {offset_y:+d}]")
        pyautogui.click(actual_x, actual_y)

        # 点击后等待
        post_delay = random.uniform(*self.after_click_wait)
        time.sleep(post_delay)
