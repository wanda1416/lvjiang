"""基础操作：点击 / point / arrow 拖拽 / 等待

定位失败（区域 / point / arrow 在当前布局没绑定、等待参数未定义）一律
抛错：这类失败意味着脚本或布局配错了，静默空转只会让后续步骤在错误
的页面上继续乱点，比当场停下来危险得多。
"""

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
            raise ValueError(
                f"场景 {scene_key} 的区域未绑定坐标: {field_key}，"
                f"请在场景布局编辑器中绑定后重试"
            )

        screen_x, screen_y = self._region_to_screen(region, jitter)
        logger.debug(f"点击: {scene_key}/{field_key} -> 屏幕({screen_x},{screen_y})")
        self._input.click_screen(screen_x, screen_y, f"{scene_key}/{field_key}")

    def click_at(self, x: int, y: int):
        """点击屏幕绝对坐标"""
        logger.debug(f"点击坐标: ({x}, {y})")
        self._input.click_screen(x, y, "dynamic")

    # ─── Point / Arrow 操作 ────────────────────────────────

    def click_any(self, scene_key: str, key: str):
        """点击 region / point / panel（自动识别，region → point → panel 顺序）"""
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
        # 尝试作为 panel 查找，点击面板中心
        panels = self._layout.get_scene_panels(scene_key)
        panel = next((p for p in panels if p.key == key), None)
        if panel is not None:
            # 面板中心点坐标
            cx = panel.x_ratio + panel.w_ratio / 2
            cy = panel.y_ratio + panel.h_ratio / 2
            screen_x, screen_y = self._ratio_to_screen(cx, cy)
            logger.debug(f"点击 panel 中心: {scene_key}/{key} -> 屏幕({screen_x},{screen_y})")
            self._input.click_screen(screen_x, screen_y, f"{scene_key}/{key}(panel)")
            return
        raise ValueError(
            f"场景 {scene_key} 的 region / point / panel 未绑定坐标: {key}，"
            f"请在场景布局编辑器中绑定后重试"
        )

    def click_point(self, scene_key: str, point_key: str):
        """点击 point 中心（带半径内随机偏移）"""
        points = self._layout.get_scene_points(scene_key)
        point = next((p for p in points if p.key == point_key), None)
        if point is None:
            raise ValueError(f"场景 {scene_key} 的坐标点未绑定: {point_key}")
        screen_x, screen_y = self._point_to_screen(point)
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
            raise ValueError(f"场景 {scene_key} 的方向未绑定: {arrow_key}")
        points = self._layout.get_scene_points(scene_key)
        from_point = next((p for p in points if p.key == arrow.from_key), None)
        if from_point is None:
            raise ValueError(f"方向 {arrow_key} 的起点坐标点未定义: {arrow.from_key}")
        # 终点：吸附态动态查 point，绝对态直接用坐标
        if arrow.to_key is not None:
            to_point = next((p for p in points if p.key == arrow.to_key), None)
            if to_point is None:
                raise ValueError(f"方向 {arrow_key} 的终点坐标点未定义: {arrow.to_key}")
            to_cx, to_cy = to_point.cx_ratio, to_point.cy_ratio
        else:
            to_cx, to_cy = arrow.to_cx_ratio, arrow.to_cy_ratio
        fx, fy = self._point_to_screen(from_point)
        tx, ty = self._ratio_to_screen(to_cx, to_cy)
        logger.debug(f"拖拽 arrow: {scene_key}/{arrow_key} ({fx},{fy})->({tx},{ty})" + (f" hold {hold}s" if hold else ""))
        self._input.drag_screen(fx, fy, tx, ty, f"{scene_key}/{arrow_key}", duration=duration, hold=hold)

    # ─── 等待 ──────────────────────────────────────────────

    def wait_delay(self, delay_name: str):
        """按命名等待参数等待（可被停止请求打断）

        参数在配置管理「等待参数」页维护（app.yaml delay_params），
        按 key 查找，在定义的范围内随机取值。
        """
        param = self._delay_params.get(delay_name)
        if param is None:
            raise ValueError(
                f"未知的等待参数: {delay_name}，请先在配置管理→等待参数中定义"
            )
        actual = random.uniform(*param.range)
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

    def wait_stable(self, timeout: float, threshold: float = 0.02,
                    interval: float = 0.3, stable_duration: float = 0.5):
        """等待画面稳定（连续截图对比）

        每 interval 秒截图一次，相邻两帧的像素差异率低于 threshold 时
        视为「画面没变」。连续稳定时长达到 stable_duration 秒后返回。
        总超时 timeout 秒内未稳定则抛出 TimeoutError。

        与 wait_seconds 相同，期间持续检查停止标志。
        """
        import cv2

        deadline = time.monotonic() + max(0.0, timeout)
        prev = None
        stable_since = None

        while time.monotonic() < deadline:
            if self._stop_check():
                logger.info("wait stable: 收到停止请求，提前结束")
                return

            img = self._capture.capture()
            if img is None:
                time.sleep(interval)
                continue

            if prev is not None:
                diff = float(cv2.absdiff(prev, img).mean()) / 255.0
                if diff < threshold:
                    if stable_since is None:
                        stable_since = time.monotonic()
                        logger.debug(f"wait stable: 差异 {diff:.4f} < {threshold}，开始计时")
                    elif time.monotonic() - stable_since >= stable_duration:
                        logger.info(f"wait stable: 画面已稳定 {stable_duration}s (差异 {diff:.4f})")
                        return
                else:
                    if stable_since is not None:
                        logger.debug(f"wait stable: 差异 {diff:.4f} >= {threshold}，重置")
                    stable_since = None

            prev = img.copy()
            remaining = deadline - time.monotonic()
            time.sleep(min(interval, max(0.0, remaining)))

        raise TimeoutError(
            f"wait stable: 画面在 {timeout}s 内未稳定（差异阈值 {threshold}）"
        )
