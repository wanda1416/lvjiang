"""位置对齐 + 三向指纹校验遍历（旧方案，供回切）"""
from __future__ import annotations

from enum import Enum
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


class ScrollState(Enum):
    """滚动校验结果（旧方案 PositionalTraversal 专用）"""
    BOTTOM = "bottom"       # 到底，结束
    PROCESS = "process"     # 有新行待处理
    SKIP = "skip"           # 本轮无新行，跳过


class PositionalTraversal(BagTraversal):
    """位置对齐 + 三向指纹校验遍历（旧方案，自 auto_tuning 原样迁入）

    严格按 tuning-mechanics.md 设计：维护 first_real_row 位置簿记，
    每次滚动后用 prev/cur/next 三向候选指纹校验步进量并小步补滚/回滚
    纠偏；未知指纹用第二行反查 + 按步进假定 + 连续假定保险丝兜底。
    """

    name = "positional"

    def __init__(self):
        # 连续「未知指纹按步进假定」的次数（防硬到底死循环的保险丝，
        # 见 _scroll_and_verify_step）；任一次指纹真实命中即清零
        self._assume_streak = 0

    def traverse(self, wf: "AutoTuningWorkflow", detail_scene: str) -> None:
        """背包滚动遍历

        状态变量：
            row_index      = 已处理过的行数
            first_real_row = 当前可见区第一行的逻辑行号（从1起）
            last_real_row  = 当前实际出现的最后一行逻辑行号
            fps            = 各逻辑行首列指纹（0 起始索引）
        """
        # 从 panel 定义获取初始行列数
        panel_obj = wf._find_panel(wf.GRID_SCENE, wf.GRID_PANEL)
        if panel_obj is None:
            logger.error(f"未找到 panel {wf.GRID_SCENE}.{wf.GRID_PANEL}")
            return
        initial_rows = panel_obj.rows
        cols = panel_obj.cols
        logger.info(f"背包遍历开始: 初始行数={initial_rows}, 列数={cols}")

        fps: list[str] = []
        self._assume_streak = 0

        # 初始扫描 = 处理第一批新行（row_index=0, first_real_row=1, real_rows=initial_rows）
        wf.align_panel(wf.GRID_SCENE, wf.GRID_PANEL)
        logger.info("═══ 初始扫描 ═══")
        row_index = self._process_new_rows(
            wf, detail_scene, fps, 0, 1, initial_rows, cols)
        if wf.slot_level_exhausted:
            logger.info("遇到低于等级门槛的有效装备 → 结束当前部位")
            return
        first_real_row = 1
        logger.info(f"初始完成: row_index={row_index} first_real_row=1 fps={fps}")
        if row_index == 0:
            logger.info("初始扫描即到底，结束")
            return

        # 主循环：滚动 → 校验 → 处理新行
        last_real_rows = 0
        scroll_round = 0
        while not wf.is_stopped and not wf.slot_level_exhausted:
            scroll_round += 1
            logger.info(f"═══ 滚动 #{scroll_round} ═══")

            state, real_rows = self._scroll_and_verify_step(
                wf, detail_scene, fps, first_real_row, initial_rows)

            if state == ScrollState.BOTTOM:
                last_real_rows = real_rows
                break
            if state == ScrollState.SKIP:
                continue

            # PROCESS：正常步进，first_real_row +1
            first_real_row += 1
            last_real_row = first_real_row + real_rows - 1
            logger.info(
                f"步进确认: first_real_row={first_real_row} "
                f"real_rows={real_rows} last_real_row={last_real_row} row_index={row_index}"
            )

            if row_index == last_real_row:
                logger.info("  无新行，本轮不处理")
                continue

            row_index = self._process_new_rows(
                wf, detail_scene, fps, row_index, first_real_row, real_rows,
                cols)
            if wf.slot_level_exhausted:
                logger.info("遇到低于等级门槛的有效装备 → 结束当前部位")
                break
            logger.info(f"  row_index → {row_index}, fps={fps}")

        # 循环结束：处理剩余可见行（到底时处理，正常退出时 row_index==last_real_row 不处理）
        if last_real_rows > 0 and not wf.slot_level_exhausted:
            final_last = first_real_row + last_real_rows - 1
            if row_index < final_last:
                row_index = self._process_new_rows(
                    wf, detail_scene, fps, row_index, first_real_row,
                    last_real_rows, cols)
                logger.info(f"  剩余行处理完毕 row_index → {row_index}, fps={fps}")

    def _process_new_rows(self, wf: "AutoTuningWorkflow", detail_scene: str,
                          fps: list[str], row_index: int, first_real_row: int,
                          real_rows: int, cols: int) -> int:
        """处理当前可见区中 row_index 之后的新行，返回更新后的 row_index。

        grid 第1行 = 逻辑行 first_real_row，win_row = logical_row - first_real_row + 1。
        每行处理第 1..cols 全部列：第 1 列兼作行指纹（滚动校验锚点）；
        第 2 列起由 _process_row_cols 遍历。
        遇到空行视为到底，立即停止（不 append 空串占位）。
        返回值 == 传入 row_index 表示没有新行被处理（调用方据此判断结束）。
        """
        last_real_row = first_real_row + real_rows - 1
        processed = row_index
        for logical_row in range(row_index + 1, last_real_row + 1):
            win_row = logical_row - first_real_row + 1
            # ── 第 1 列：读取兼作行指纹 ──
            name, fp, equip = wf._read_row(detail_scene, win_row)
            if not fp:
                logger.info(f"  新行{logical_row} grid[{win_row}][1] 空 slot → 到底")
                break
            fps.append(fp)
            logger.info(f"  新行{logical_row} grid[{win_row}][1] {name} fp={fp}")
            new_fp = wf._process_equipment(name, equip, detail_scene,
                                           row=win_row)
            if wf.slot_level_exhausted:
                logger.info(f"  新行{logical_row} 命中等级终止条件")
                break
            # 调律会给该行首列装备加词条 → 指纹变化；用处理后指纹覆盖。
            # 回收后由 _process_equipment 就地续处理补位，new_fp 为最终
            # 占位者指纹；空串表示该格已空（背包尽头）。
            if not new_fp:
                fps.pop()
                logger.info(f"  行{logical_row} 回收后已空 → 到底")
                break
            if new_fp != fp:
                logger.info(f"  行{logical_row} 处理后指纹更新: {fp} → {new_fp}")
                fps[-1] = new_fp
            # ── 第 2..cols 列：遍历本行剩余列 ──
            wf._process_row_cols(detail_scene, win_row, logical_row, cols)
            if wf.slot_level_exhausted:
                break
            processed = logical_row
        return processed

    def _scroll_and_verify_step(self, wf: "AutoTuningWorkflow",
                                detail_scene: str, fps: list[str],
                                first_real_row: int,
                                panel_rows: int) -> tuple[ScrollState, int]:
        """滚动一行 + 三向校验 + 小步修正，返回 (状态, 当前可见行数)。

        正常步进：返回 PROCESS。
        滚少了：补滚后窗口仍满行（n_rows == panel_rows，拖拽已无效果）
                → 第 2 次即确认到底；窗口非满行（真滚少了）→ 继续补滚，
                连续 3 次仍滚少则按到底收束并记录异常。
        连续 3 次滚多了：抛 RuntimeError（不应发生）。
        未知 nfp：滚动一行后首行必然是已见过的装备（每轮可见区全部
                 行都记入 fps），未知指纹只可能是 OCR 抖动致漂移。先用
                 第二可见行反查：确认步进则更新漂移指纹继续；确认未
                 步进则并入滚少了流程；反查无果按步进假定（刷新指纹），
                 连续假定 2 次视为指纹表错位，按到底收束防死循环。
        """
        # 前沿越界守卫：假定步进后若未发现任何新行，first_real_row 可能
        # 已越过全部已知行，再滚动已无候选可比 → 直接按到底收束
        if first_real_row >= len(fps):
            logger.info(
                f"  first_real_row={first_real_row} 已越过全部已知行"
                f"（len(fps)={len(fps)}）→ 到底")
            return ScrollState.BOTTOM, 0

        # 大幅步进（1 行）
        wf.drag_grid(wf.GRID_SCENE, wf.GRID_PANEL, "up", hold=0.3)
        wf.wait_stable("scroll_settle")  # 滚动后面板稳定
        alignment_raw = wf.align_panel(wf.GRID_SCENE, wf.GRID_PANEL)
        assert alignment_raw is not None, tr("对齐失败：alignment 为 None")
        alignment: GridAlignment = alignment_raw

        # 对比现场：三向校验的候选指纹（next 在前沿处不存在）
        next_fp = (fps[first_real_row + 1]
                   if first_real_row + 1 < len(fps) else tr("无"))
        logger.info(
            f"  滚动校验: first_real_row={first_real_row} len(fps)={len(fps)} "
            f"窗口行数={alignment.n_rows} 候选 prev={fps[first_real_row - 1]} "
            f"cur={fps[first_real_row]} next={next_fp}")

        short_count = 0
        long_count = 0
        while not wf.is_stopped:
            _, nfp, equip1 = wf._read_row(detail_scene, 1)

            # 空指纹 = 空行 = 到底（每行第一列不可能为空，空即超出最后一行）
            if not nfp:
                logger.info("  空指纹 → 空行 → 到底")
                return ScrollState.BOTTOM, alignment.n_rows

            if nfp == fps[first_real_row]:
                logger.info(f"  正常步进: nfp={nfp} == fps[{first_real_row}]")
                self._assume_streak = 0
                return ScrollState.PROCESS, alignment.n_rows
            elif nfp == fps[first_real_row - 1]:
                short_count += 1
                self._assume_streak = 0
                logger.info(
                    f"  滚少了({short_count}): nfp={nfp} == "
                    f"fps[{first_real_row - 1}] 读到={wf._equip_label(equip1)} "
                    f"窗口行数={alignment.n_rows}/{panel_rows}")
                if short_count >= 2 and alignment.n_rows >= panel_rows:
                    # 补滚后窗口仍满行且首行未动 → 拖拽已无效果 = 硬到底，
                    # 无需等满 3 次，第 2 次即可确认结束
                    logger.info("  补滚后仍满行且首行未动 → 到底（提前确认）")
                    return ScrollState.BOTTOM, alignment.n_rows
                if short_count >= 3:
                    # 窗口非满行说明确实滚少了，补滚两次仍未步进不应发生：
                    # 按到底收束避免死循环，但记录异常备查
                    logger.error(
                        f"  异常：连续3次滚少了且窗口非满行"
                        f"({alignment.n_rows}/{panel_rows})，按到底收束："
                        f"nfp={nfp} first_real_row={first_real_row} fps={fps}")
                    return ScrollState.BOTTOM, alignment.n_rows
                wf.drag_grid(wf.GRID_SCENE, wf.GRID_PANEL, "up", distance=0.25)
                wf.wait_stable("scroll_settle")  # 补滚后稳定
                alignment_raw = wf.align_panel(wf.GRID_SCENE, wf.GRID_PANEL)
                assert alignment_raw is not None, tr("补滚后对齐失败")
                alignment = alignment_raw
            elif (first_real_row + 1 < len(fps)
                    and nfp == fps[first_real_row + 1]):
                # 边界守卫：前沿处（first_real_row 为最后一个已知行）无 next
                # 候选，直接取下标会越界；未知指纹应落入 else 诊断分支。
                long_count += 1
                self._assume_streak = 0
                logger.info(
                    f"  滚多了({long_count}/3): nfp={nfp} == "
                    f"fps[{first_real_row + 1}] 读到={wf._equip_label(equip1)}，"
                    f"回滚 0.25")
                if long_count >= 3:
                    raise RuntimeError(
                        f"连续3次判定滚多了，不应发生："
                        f"nfp={nfp} first_real_row={first_real_row} fps={fps}"
                    )
                wf.drag_grid(wf.GRID_SCENE, wf.GRID_PANEL, "down", distance=0.25)
                wf.wait_stable("scroll_settle")  # 回滚后稳定
                alignment = wf.align_panel(wf.GRID_SCENE, wf.GRID_PANEL)  # type: ignore[assignment]
            else:
                # nfp 与三候选都不匹配：滚动一行后首行必然是已见过的装备
                # （每轮 _process_new_rows 会把可见区全部行记入 fps），故
                # 未知指纹只可能是首列 OCR 抖动（词条读空/数值漂移）致
                # 指纹漂移。用第二可见行反查区分「正常步进」与「未步进
                # （硬到底）」：
                label = wf._equip_label(equip1)
                _, nfp2, equip2 = wf._read_row(detail_scene, 2)
                if nfp2 and first_real_row + 1 < len(fps) \
                        and nfp2 == fps[first_real_row + 1]:
                    logger.warning(
                        f"  首行指纹漂移但滚动正常：nfp={nfp} ≠ "
                        f"fps[{first_real_row}]={fps[first_real_row]}，"
                        f"第二行 {nfp2} == fps[{first_real_row + 1}]，"
                        f"更新指纹并按正常步进继续"
                    )
                    self._assume_streak = 0
                    fps[first_real_row] = nfp
                    return ScrollState.PROCESS, alignment.n_rows
                if nfp2 and nfp2 == fps[first_real_row]:
                    # 第二行仍是滚动前的第二行 → 未步进、仅首行漂移：
                    # 刷新原首行指纹后并入「滚少了」流程（补滚/到底收束）
                    short_count += 1
                    self._assume_streak = 0
                    logger.warning(
                        f"  首行指纹漂移且未步进({short_count})：nfp={nfp} "
                        f"读到={label}，第二行 {nfp2} == fps[{first_real_row}]，"
                        f"刷新 fps[{first_real_row - 1}] 后按滚少了处理")
                    fps[first_real_row - 1] = nfp
                    if short_count >= 2 and alignment.n_rows >= panel_rows:
                        logger.info("  补滚后仍满行且未步进 → 到底（提前确认）")
                        return ScrollState.BOTTOM, alignment.n_rows
                    if short_count >= 3:
                        logger.error(
                            f"  异常：连续3次未步进且窗口非满行"
                            f"({alignment.n_rows}/{panel_rows})，按到底收束："
                            f"nfp={nfp} first_real_row={first_real_row} fps={fps}")
                        return ScrollState.BOTTOM, alignment.n_rows
                    wf.drag_grid(wf.GRID_SCENE, wf.GRID_PANEL, "up",
                                 distance=0.25)
                    wf.wait_stable("scroll_settle")  # 漂移补滚后稳定
                    alignment_raw = wf.align_panel(wf.GRID_SCENE, wf.GRID_PANEL)
                    assert alignment_raw is not None, tr("漂移补滚后对齐失败")
                    alignment = alignment_raw
                    continue
                # 反查无果（前沿无候选/第二行为空/两行都漂移）→ 指纹检测
                # 的目的只是确认滚动了一行，既然读到了装备就假定步进成立，
                # 刷新该位置指纹继续；连续第 2 次落入假定说明指纹表疑似已
                # 错位（如硬到底且首行持续漂移），按到底收束防止无限循环
                self._assume_streak += 1
                if self._assume_streak >= 2:
                    logger.error(
                        f"  连续 {self._assume_streak} 次未知指纹，指纹表疑似"
                        f"错位，按到底收束：首行={label} nfp={nfp} "
                        f"第二行={wf._equip_label(equip2)} nfp2={nfp2 or '空'} "
                        f"first_real_row={first_real_row} fps={fps}")
                    return ScrollState.BOTTOM, alignment.n_rows
                logger.warning(
                    f"  未知指纹按步进假定({self._assume_streak}/2)："
                    f"首行={label} nfp={nfp} "
                    f"第二行={wf._equip_label(equip2)} nfp2={nfp2 or '空'}，"
                    f"刷新 fps[{first_real_row}] 后继续")
                fps[first_real_row] = nfp
                return ScrollState.PROCESS, alignment.n_rows
        return ScrollState.SKIP, 0  # is_stopped 中断
