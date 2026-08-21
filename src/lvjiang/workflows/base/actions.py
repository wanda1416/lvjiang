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

    def click_region(self, scene_key: str, field_key: str, jitter: bool = True, **kw):
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
        self._input.click_screen(screen_x, screen_y, f"{scene_key}/{field_key}", **kw)

    def click_at(self, x: int, y: int, **kw):
        """点击屏幕绝对坐标"""
        logger.debug(f"点击坐标: ({x}, {y})")
        self._input.click_screen(x, y, "dynamic", **kw)

    # ─── Point / Arrow 操作 ────────────────────────────────

    def click_any(self, scene_key: str, key: str, **kw):
        """点击 region / point / panel（自动识别，region → point → panel 顺序）"""
        regions = self._layout.get_scene_regions(scene_key)
        region = next((r for r in regions if r.key == key), None)
        if region is not None:
            self.click_region(scene_key, key, **kw)
            return
        points = self._layout.get_scene_points(scene_key)
        point = next((p for p in points if p.key == key), None)
        if point is not None:
            self.click_point(scene_key, key, **kw)
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
            self._input.click_screen(screen_x, screen_y, f"{scene_key}/{key}(panel)", **kw)
            return
        raise ValueError(
            f"场景 {scene_key} 的 region / point / panel 未绑定坐标: {key}，"
            f"请在场景布局编辑器中绑定后重试"
        )

    def move_any(self, scene_key: str, key: str):
        """移动鼠标到 region / point / panel 中心（自动识别，region → point → panel 顺序）"""
        regions = self._layout.get_scene_regions(scene_key)
        region = next((r for r in regions if r.key == key), None)
        if region is not None:
            screen_x, screen_y = self._region_to_screen(region, jitter=True)
            logger.debug(f"移动: {scene_key}/{key} -> 屏幕({screen_x},{screen_y})")
            self._input.move_screen(screen_x, screen_y, f"{scene_key}/{key}")
            return
        points = self._layout.get_scene_points(scene_key)
        point = next((p for p in points if p.key == key), None)
        if point is not None:
            screen_x, screen_y = self._point_to_screen(point)
            logger.debug(f"移动 point: {scene_key}/{key} -> 屏幕({screen_x},{screen_y})")
            self._input.move_screen(screen_x, screen_y, f"{scene_key}/{key}")
            return
        panels = self._layout.get_scene_panels(scene_key)
        panel = next((p for p in panels if p.key == key), None)
        if panel is not None:
            cx = panel.x_ratio + panel.w_ratio / 2
            cy = panel.y_ratio + panel.h_ratio / 2
            screen_x, screen_y = self._ratio_to_screen(cx, cy)
            logger.debug(f"移动 panel 中心: {scene_key}/{key} -> 屏幕({screen_x},{screen_y})")
            self._input.move_screen(screen_x, screen_y, f"{scene_key}/{key}(panel)")
            return
        raise ValueError(
            f"场景 {scene_key} 的 region / point / panel 未绑定坐标: {key}，"
            f"请在场景布局编辑器中绑定后重试"
        )

    def click_point(self, scene_key: str, point_key: str, **kw):
        """点击 point 中心（带半径内随机偏移）"""
        points = self._layout.get_scene_points(scene_key)
        point = next((p for p in points if p.key == point_key), None)
        if point is None:
            raise ValueError(f"场景 {scene_key} 的坐标点未绑定: {point_key}")
        screen_x, screen_y = self._point_to_screen(point)
        logger.debug(f"点击 point: {scene_key}/{point_key} -> 屏幕({screen_x},{screen_y})")
        self._input.click_screen(screen_x, screen_y, f"{scene_key}/{point_key}", **kw)

    def drag_arrow(self, scene_key: str, arrow_key: str, duration: float | tuple[float, float] | None = None, hold: float | None = None, **kw):
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
        self._input.drag_screen(fx, fy, tx, ty, f"{scene_key}/{arrow_key}", duration=duration, hold=hold, **kw)

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

    def wait_stable(self, timeout: float | str, threshold: float = 0.02,
                    interval: float = 0.3, stable_duration: float = 0.5,
                    least: float = 0.5, crop_box: dict | None = None,
                    *, _clock=time.monotonic, _sleep=time.sleep):
        """等待画面稳定（连续截图对比）

        每 interval 秒截图一次，相邻两帧的像素差异率低于 threshold 时
        视为「画面没变」。连续稳定时长达到 stable_duration 秒后返回。

        crop_box: 像素裁剪框 {'x', 'y', 'w', 'h'}，只对指定区域做 diff 对比。
        用于半屏 UI + 半屏动画场景，避免游戏画面动画干扰稳定检测。
        为 None 时检测全画面（向后兼容）。

        timeout 是等待预算而非硬性断言：预算内未稳定时记录警告并继续
        执行（游戏画面常有持续动画，永远达不到阈值是常态，不应终止流程）。
        实际书写时建议把 timeout 设得宽裕些，至少不会比固定等待差。

        timeout 可以是数值（秒）或命名延迟参数名（如 "page_refresh"），
        命名参数从 delay_params 配置取范围中值。

        least 参数：点击后至少等待这么久再开始稳定检测。
        防止转场动画未开始就被误判为「画面已稳定」。

        与 wait_seconds 相同，期间持续检查停止标志。
        """
        import cv2

        # 命名延迟参数：取范围中值
        if isinstance(timeout, str):
            delay_param = self._delay_params.get(timeout)
            if delay_param is None:
                raise ValueError(
                    f"wait stable: 等待参数 @{timeout} 未定义"
                )
            lo, hi = delay_param.range
            timeout_val = (lo + hi) / 2.0
        else:
            timeout_val = float(timeout)

        start_time = _clock()
        deadline = start_time + max(0.0, timeout_val)
        least_until = start_time + max(0.0, least)
        prev = None
        stable_since = None

        while _clock() < deadline:
            if self._stop_check():
                logger.debug("wait stable: 收到停止请求，提前结束")
                return

            img = self._capture.capture()
            if img is None:
                _sleep(interval)
                continue

            # 区域限定：裁剪到指定区域后再做 diff 对比
            if crop_box is not None:
                x, y, w, h = crop_box["x"], crop_box["y"], crop_box["w"], crop_box["h"]
                ih, iw = img.shape[:2]
                x1, y1 = max(0, min(x, iw)), max(0, min(y, ih))
                x2, y2 = max(0, min(x + w, iw)), max(0, min(y + h, ih))
                if x2 <= x1 or y2 <= y1:
                    # 裁剪区域无效，回退到全画面
                    pass
                else:
                    img = img[y1:y2, x1:x2]

            # least 期间：只截图建立基准，不判断稳定
            if prev is not None and _clock() >= least_until:
                diff = float(cv2.absdiff(prev, img).mean()) / 255.0
                if diff < threshold:
                    if stable_since is None:
                        stable_since = _clock()
                        logger.debug(f"wait stable: 差异 {diff:.4f} < {threshold}，开始计时")
                    elif _clock() - stable_since >= stable_duration:
                        elapsed = _clock() - start_time
                        logger.debug(f"wait stable: 画面已稳定 {elapsed:.1f}s (差异 {diff:.4f})")
                        return
                else:
                    if stable_since is not None:
                        logger.debug(f"wait stable: 差异 {diff:.4f} >= {threshold}，重置")
                    stable_since = None

            prev = img.copy()
            remaining = deadline - _clock()
            _sleep(min(interval, max(0.0, remaining)))

        elapsed = _clock() - start_time
        logger.warning(
            f"wait stable: 画面在 {elapsed:.1f}s 内未稳定（差异阈值 {threshold}），继续执行"
        )
