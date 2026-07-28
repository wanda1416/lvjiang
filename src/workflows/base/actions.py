"""基础操作：点击 / point / arrow 拖拽 / 等待"""

import random
import time

from loguru import logger


class _ActionMixin:
    """点击、拖拽与等待原语"""

    # ─── 点击操作 ──────────────────────────────────────────

    def click_region(self, scene_key: str, field_key: str, jitter: bool = True):
        """点击指定场景中指定区域的中心（带随机抖动）"""
        regions = self._layout.get_scene_regions(scene_key)
        region = next((r for r in regions if r.key == field_key), None)
        if region is None:
            logger.error(f"场景 {scene_key} 没有定义区域: {field_key}")
            return

        screen_x, screen_y = self._region_to_screen(region, jitter)
        if screen_x is None:
            return

        logger.debug(f"点击: {scene_key}/{field_key} -> 屏幕({screen_x},{screen_y})")
        self._input.click_screen(screen_x, screen_y, f"{scene_key}/{field_key}")

    def click_at(self, x: int, y: int):
        """点击屏幕绝对坐标"""
        logger.debug(f"点击坐标: ({x}, {y})")
        self._input.click_screen(x, y, "dynamic")

    # ─── Point / Arrow 操作 ────────────────────────────────

    def click_any(self, scene_key: str, key: str):
        """点击 region 或 point（自动识别，region 优先）"""
        regions = self._layout.get_scene_regions(scene_key)
        region = next((r for r in regions if r.key == key), None)
        if region is not None:
            self.click_region(scene_key, key)
            return
        points = self._layout.get_scene_points(scene_key)
        point = next((p for p in points if p.key == key), None)
        if point is not None:
            self.click_point(scene_key, key)
            return
        logger.error(f"场景 {scene_key} 没有定义 region 或 point: {key}")

    def click_point(self, scene_key: str, point_key: str):
        """点击 point 中心（带半径内随机偏移）"""
        points = self._layout.get_scene_points(scene_key)
        point = next((p for p in points if p.key == point_key), None)
        if point is None:
            logger.error(f"场景 {scene_key} 没有定义 point: {point_key}")
            return
        screen_x, screen_y = self._point_to_screen(point)
        if screen_x is None:
            return
        logger.debug(f"点击 point: {scene_key}/{point_key} -> 屏幕({screen_x},{screen_y})")
        self._input.click_screen(screen_x, screen_y, f"{scene_key}/{point_key}")

    def drag_arrow(self, scene_key: str, arrow_key: str, duration: float | tuple[float, float] | None = None, hold: float | None = None):
        """执行 arrow 定义的拖拽

        Args:
            duration: 拖拽移动时长（秒）。单值固定，二元组则范围内随机。None 使用默认值。
            hold: 到达目标后按住不放的时长（秒）。None 表示不按住。
        """
        arrows = self._layout.get_scene_arrows(scene_key)
        arrow = next((a for a in arrows if a.key == arrow_key), None)
        if arrow is None:
            logger.error(f"场景 {scene_key} 没有定义 arrow: {arrow_key}")
            return
        points = self._layout.get_scene_points(scene_key)
        from_point = next((p for p in points if p.key == arrow.from_key), None)
        if from_point is None:
            logger.error(f"arrow {arrow_key} 的起点 point {arrow.from_key} 未定义")
            return
        # 终点：吸附态动态查 point，绝对态直接用坐标
        if arrow.to_key is not None:
            to_point = next((p for p in points if p.key == arrow.to_key), None)
            if to_point is None:
                logger.error(f"arrow {arrow_key} 的终点 point {arrow.to_key} 未定义")
                return
            to_cx, to_cy = to_point.cx_ratio, to_point.cy_ratio
        else:
            to_cx, to_cy = arrow.to_cx_ratio, arrow.to_cy_ratio
        fx, fy = self._point_to_screen(from_point)
        tx, ty = self._ratio_to_screen(to_cx, to_cy)
        if fx is None or tx is None:
            return
        logger.debug(f"拖拽 arrow: {scene_key}/{arrow_key} ({fx},{fy})->({tx},{ty})" + (f" hold {hold}s" if hold else ""))
        self._input.drag_screen(fx, fy, tx, ty, f"{scene_key}/{arrow_key}", duration=duration, hold=hold)

    # ─── 等待 ──────────────────────────────────────────────

    def wait_delay(self, delay_name: str):
        """按命名等待参数等待（可被停止请求打断）

        参数在配置管理「等待参数」页维护（DelayConfig.custom），
        按 key 查找，在定义的范围内随机取值。
        """
        custom = self._delay.custom.get(delay_name)
        if custom is None:
            logger.error(f"未知的等待参数: {delay_name}（请在配置管理→等待参数中定义）")
            return
        actual = random.uniform(*custom.range)
        logger.debug(f"等待 {delay_name} = {actual:.2f}s")
        self.wait_seconds(actual)

    def wait_seconds(self, seconds: float):
        """固定等待（可被停止请求打断）

        用 50ms 轮询代替 time.sleep 阻塞，期间持续检查停止标志，
        使 F10 / 停止按钮能在等待期间立即生效，而不是等整段 sleep 结束。
        """
        logger.debug(f"等待 {seconds}s")
        deadline = time.monotonic() + max(0.0, seconds)
        while time.monotonic() < deadline:
            if self._stop_check():
                logger.info("等待期间收到停止请求，提前结束")
                return
            remaining = deadline - time.monotonic()
            time.sleep(min(0.05, max(0.0, remaining)))
