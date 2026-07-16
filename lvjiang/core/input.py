"""输入控制模块 - 封装 pyautogui 点击，模拟人类操作"""

import random
import time

import pyautogui
from loguru import logger

from ..config import DelayConfig


class InputController:
    """输入控制器（所有点击延迟参数统一从 DelayConfig 读取）
    
    公开接口：click_screen / drag_screen
    """

    def __init__(self, delay_config: DelayConfig | None = None):
        cfg = delay_config or DelayConfig()
        self.before_click_wait = cfg.before_click_wait
        self.after_click_wait = cfg.after_click_wait
        self.mouse_move_duration = cfg.mouse_move_duration
        self.click_random_offset = cfg.click_random_offset
        self.region_jitter_ratio = cfg.region_jitter_ratio
        # pyautogui 安全设置
        # FAILSAFE 禁用：自动化期间鼠标移动由程序控制，
        # 启用 FAILSAFE 会在鼠标偶然经过屏幕角落时抛 FailSafeException，
        # 导致 QThread 硬崩溃（进程闪退）。停止操作由工作流 stop_check 控制。
        pyautogui.FAILSAFE = False
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
        try:
            pyautogui.moveTo(x, y, duration=duration)
        except Exception as e:
            logger.error(f"鼠标移动失败 ({x},{y}): {e}")
            raise

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
        try:
            pyautogui.click(actual_x, actual_y)
        except Exception as e:
            logger.error(f"点击失败 ({actual_x},{actual_y}): {e}")
            raise

        # 点击后等待
        post_delay = random.uniform(*self.after_click_wait)
        time.sleep(post_delay)

    def drag_screen(self, from_x: int, from_y: int, to_x: int, to_y: int, poi_name: str = "",
                    duration: float | tuple[float, float] | None = None):
        """从起点拖拽到终点（模拟人类操作）

        Args:
            duration: 移动时长（秒）。单值固定，二元组则范围内随机。None 使用默认 mouse_move_duration。
        """
        self._move_to(from_x, from_y)
        pre_delay = random.uniform(*self.before_click_wait)
        time.sleep(pre_delay)
        # 解析时长
        if duration is None:
            move_dur = random.uniform(*self.mouse_move_duration)
        elif isinstance(duration, tuple):
            move_dur = random.uniform(*duration)
        else:
            move_dur = float(duration)
        logger.debug(f"拖拽 {poi_name}: ({from_x},{from_y}) -> ({to_x},{to_y}) [{move_dur:.2f}s]")
        pyautogui.moveTo(from_x, from_y, duration=move_dur)
        pyautogui.mouseDown()
        pyautogui.moveTo(to_x, to_y, duration=move_dur)
        pyautogui.mouseUp()
        post_delay = random.uniform(*self.after_click_wait)
        time.sleep(post_delay)
