"""滑动窗口指纹去重遍历（新方案，默认）"""
from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

from lvjiang.apps.yysls.workflows.implementations.bag_traversal.base import (
    BagTraversal,
)
from lvjiang.workflows.align import GridAlignment

from ......i18n import tr

if TYPE_CHECKING:  # pragma: no cover - 仅类型标注，防循环导入
    from lvjiang.apps.yysls.workflows.implementations.auto_tuning import (
        AutoTuningWorkflow,
    )


class DedupTraversal(BagTraversal):
    """滑动窗口指纹去重遍历（新方案，默认）

    关键性质：
    - 去重只对比「上一轮窗口」的指纹（相邻两轮对同一物理行的读取，
      时间近、条件同，漂移概率最低；全历史累积会让背包中两件完全
      同款的装备指纹撞车而漏件）。
    - 滚动量 ≤ 窗口行数时新行之前必然是上一窗见过的旧行 → 滚多/滚少
      都被去重自然吸收，零遗漏，无需任何补滚/回滚纠偏。
    - 指纹漂移 → 误判新行 → 重复处理一次（对已处理装备无副作用），
      且最新指纹随窗口刷新自愈，不会持续误判。

    到底判定（三条规则）：
    - 读到空行（首列指纹为空）→ 背包尽头，立即结束；
    - 非满窗且本轮零新行 → 到底；
    - 满窗连续 MAX_IDLE_ROUNDS 轮零新行 → 拖拽已无效果（硬到底）；
    - 另有 MAX_ROUNDS 总轮数上限作最终保险丝。
    """

    name = "dedup"

    MAX_IDLE_ROUNDS = 2     # 满窗连续零新行 → 硬到底
    MAX_ROUNDS = 200        # 总滚动轮数保险丝

    def __init__(self):
        self._seq = 0       # 已处理新行计数（日志/列遍历的逻辑行号）

    def traverse(self, wf: "AutoTuningWorkflow", detail_scene: str) -> None:
        panel_obj = wf._find_panel(wf.GRID_SCENE, wf.GRID_PANEL)
        if panel_obj is None:
            logger.error(f"未找到 panel {wf.GRID_SCENE}.{wf.GRID_PANEL}")
            return
        panel_rows = panel_obj.rows
        cols = panel_obj.cols
        logger.info(f"背包遍历开始(dedup): 窗口行数={panel_rows}, 列数={cols}")

        wf.align_panel(wf.GRID_SCENE, wf.GRID_PANEL)
        logger.info("═══ 初始扫描 ═══")
        recent, new_count, hit_empty = self._scan_window(
            wf, detail_scene, panel_rows, cols, [])
        if wf.slot_level_exhausted:
            logger.info("命中槽位级终止条件 → 结束当前部位")
            return
        if hit_empty:
            logger.info(f"初始扫描读到空行 → 背包尽头（共 {self._seq} 行）")
            return
        if new_count == 0:
            logger.info("初始扫描无装备，结束")
            return

        idle = 0
        rounds = 0
        while not wf.is_stopped:
            rounds += 1
            if rounds > self.MAX_ROUNDS:
                logger.error(
                    f"滚动轮数超过上限 {self.MAX_ROUNDS}，强制收束"
                    f"（已处理 {self._seq} 行）")
                break
            logger.info(f"═══ 滚动 #{rounds} ═══")
            wf.drag_grid(wf.GRID_SCENE, wf.GRID_PANEL, "up", hold=0.3)
            wf.wait_stable("scroll_settle")  # 滚动后面板稳定
            alignment_raw = wf.align_panel(wf.GRID_SCENE, wf.GRID_PANEL)
            assert alignment_raw is not None, tr("对齐失败：alignment 为 None")
            alignment: GridAlignment = alignment_raw
            recent, new_count, hit_empty = self._scan_window(
                wf, detail_scene, alignment.n_rows, cols, recent)
            if wf.slot_level_exhausted:
                logger.info("命中槽位级终止条件 → 结束当前部位")
                break
            if hit_empty:
                logger.info("读到空行 → 背包尽头，结束遍历")
                break
            if new_count == 0:
                if alignment.n_rows < panel_rows:
                    logger.info(
                        f"非满窗({alignment.n_rows}/{panel_rows})且零新行 → 到底")
                    break
                idle += 1
                logger.info(f"满窗零新行({idle}/{self.MAX_IDLE_ROUNDS})")
                if idle >= self.MAX_IDLE_ROUNDS:
                    logger.info("连续拖拽无新行 → 滚不动了，结束遍历")
                    break
            else:
                idle = 0
        logger.info(f"遍历结束：共处理 {self._seq} 行")

    def _scan_window(self, wf: "AutoTuningWorkflow", detail_scene: str,
                     n_rows: int, cols: int,
                     recent: list[str]) -> tuple[list[str], int, bool]:
        """扫描当前窗口全部行：对上一轮窗口指纹 recent 去重，处理新行。

        返回 (本轮窗口各行首列指纹, 新行数, 是否读到空行)。
        重复行也用最新读数入窗（漂移自愈）；新行处理完若装备被调律
        改动（返回指纹 ≠ 读入指纹），回读首列存实际 OCR 指纹作为
        下一轮去重锚点，未改动行直接存刚读到的指纹（省一次点击+OCR）。
        """
        window: list[str] = []
        new_count = 0
        for row in range(1, n_rows + 1):
            if wf.is_stopped:
                break
            name, fp, equip = wf._read_row(detail_scene, row)
            if wf.slot_level_exhausted:
                wf._close_equipment_detail()
                logger.info("  检测到当前部位槽位级终止条件 → 结束遍历")
                return window, new_count, False
            if not fp:
                logger.info(f"  grid[{row}][1] 空 slot → 背包尽头")
                return window, new_count, True
            if fp in recent:
                wf._close_equipment_detail()
                logger.info(f"  grid[{row}][1] {name} fp={fp} 已见过 → 跳过")
                window.append(fp)
                continue
            new_count += 1
            self._seq += 1
            logger.info(f"  新行{self._seq} grid[{row}][1] {name} fp={fp}")
            if wf._equipment_read_state(equip) == "valid":
                new_fp = wf._process_equipment(
                    name, equip, detail_scene, row=row)
            else:
                # _read_row 已记录异常并关闭详情；保留内部锚点继续遍历。
                new_fp = fp
            if wf.slot_level_exhausted:
                wf._close_equipment_detail()
                return window, new_count, False
            if not new_fp:
                # 回收链后该格已空（无装备补位）→ 背包尽头
                if wf.is_stopped:
                    break
                logger.info(f"  grid[{row}][1] 回收后已空 → 背包尽头")
                return window, new_count, True
            wf._process_row_cols(detail_scene, row, self._seq, cols)
            if wf.slot_level_exhausted:
                return window, new_count, False
            if new_fp != fp:
                # 调律/回收改动了该格 → 回读首列取实际 OCR 指纹
                _, refp, _ = wf._read_row(detail_scene, row)
                wf._close_equipment_detail()
                if wf.slot_level_exhausted:
                    return window, new_count, False
                logger.info(
                    f"  行{self._seq} 处理后指纹回读: {fp} → {refp or new_fp}")
                window.append(refp or new_fp)
            else:
                window.append(fp)
        return window, new_count, False
