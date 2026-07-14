"""输入控制模块 - 封装 pyautogui 点击，模拟人类操作"""

import random
import time

import pyautogui
from loguru import logger

from ..constants import DEFAULT_CLICK_INTERVAL, DEFAULT_AFTER_CLICK_WAIT, CLICK_RANDOM_OFFSET


class InputController:
    """输入控制器"""

    def __init__(
        self,
        click_interval: tuple[float, float] = DEFAULT_CLICK_INTERVAL,
        after_click_wait: tuple[float, float] = DEFAULT_AFTER_CLICK_WAIT,
        random_offset: int = CLICK_RANDOM_OFFSET,
    ):
        self.click_interval = click_interval
        self.after_click_wait = after_click_wait
        self.random_offset = random_offset
        # pyautogui 安全设置
        pyautogui.FAILSAFE = True  # 鼠标移到左上角可中断
        pyautogui.PAUSE = 0.05

    def click(self, x: int, y: int, poi_name: str = ""):
        """
        点击指定坐标（加入随机偏移和延迟模拟人类）
        poi_name: 可选的 POI 名称，用于日志
        """
        # 随机偏移
        offset_x = random.randint(-self.random_offset, self.random_offset)
        offset_y = random.randint(-self.random_offset, self.random_offset)
        actual_x = x + offset_x
        actual_y = y + offset_y

        # 点击前延迟（模拟人类反应时间）
        pre_delay = random.uniform(*self.click_interval)
        time.sleep(pre_delay)

        # 执行点击
        label = f"({poi_name})" if poi_name else ""
        logger.debug(f"点击 {label}: ({actual_x}, {actual_y}) [偏移: +{offset_x}, +{offset_y}]")
        pyautogui.click(actual_x, actual_y)

        # 点击后等待
        post_delay = random.uniform(*self.after_click_wait)
        time.sleep(post_delay)

    def click_region_center(self, left: int, top: int, width: int, height: int, poi_name: str = ""):
        """点击区域中心"""
        cx = left + width // 2
        cy = top + height // 2
        self.click(cx, cy, poi_name)

    def move_to(self, x: int, y: int, duration: float = 0.2):
        """移动鼠标到指定位置"""
        pyautogui.moveTo(x, y, duration=duration)

    def get_mouse_position(self) -> tuple[int, int]:
        """获取当前鼠标位置"""
        pos = pyautogui.position()
        return (pos.x, pos.y)
