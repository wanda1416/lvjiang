"""调律进度信号桥 —— worker 线程 → 主线程的安全数据通道

AutoTuningWorkflow 在 worker 线程中运行，进度对话框在主线程中显示。
TuningProgressHub 作为 QObject 持有 pyqtSignal，worker 线程发射信号后
Qt 自动经 QueuedConnection 投递到主线程槽函数，无需显式加锁。

注入链路：
  tuning_tab.configure() → engine._progress_hub = hub
  auto_tuning → self.engine._progress_hub.emit(...)
  TuningProgressDialog 连接 hub 信号 → 更新 UI
"""
from __future__ import annotations

from PyQt6.QtCore import QObject, pyqtSignal

from ....i18n import tr

# 部位 key → 中文名（工作流与对话框共享，避免各自维护）
SLOT_NAMES: dict[str, str] = {
    "main_weapon": tr("主武器"), "sub_weapon": tr("副武器"),
    "ring": tr("环"), "pendant": tr("佩"),
    "head": tr("冠胄"), "chest": tr("胸甲"), "leg": tr("胫甲"), "wrist": tr("腕甲"),
}


class TuningProgressHub(QObject):
    """调律进度信号桥（worker 线程 → 主线程）

    所有信号从 worker 线程发射，经 Qt QueuedConnection 自动
    投递到主线程的 TuningProgressDialog 槽函数。
    参数只用基础类型（str/int/bool）+ object（dict/list），
    确保跨线程元类型注册安全。
    """

    # ─── 部位切换 ────────────────────────────────────────
    slot_entered = pyqtSignal(str, str)
    # (slot_key, slot_name_cn)

    # ─── 装备处理开始（OCR 扫描完成 + 潜力判定完成）─────
    equipment_started = pyqtSignal(object)
    # dict: {name, type, level, quality, affixes: list[dict],
    #        expect_rating, target_affixes: list[str]}

    # ─── 单轮调律结果 ────────────────────────────────────
    tune_round_completed = pyqtSignal(object)
    # dict: {round_no, new_affix: dict|None, food_used, food_reason,
    #        material_stock: dict, current_affixes: list[dict], affix_count}

    # ─── 扫描处理决策（评级未达门槛 / 词条已满）──────────
    scan_decision = pyqtSignal(object)
    # dict: {name, action, reason}
    # action: "recycled" | "kept" | "force_tune" | "tune_full_recycle"

    # ─── 装备处理结束 ────────────────────────────────────
    equipment_finished = pyqtSignal(object)
    # dict: {name, final_rating, rounds, affix_count,
    #        final_affixes: list[dict], status}

    # ─── 批次进度 ────────────────────────────────────────
    batch_progress = pyqtSignal(object)
    # dict: {current_slot, slot_index, total_slots}

    # ─── 整体完成 ────────────────────────────────────────
    tuning_finished = pyqtSignal(object)
    # dict: {total_equipment, total_rounds, interrupted}

    # ─── 工作流状态消息（材料不足确认等交互事件）──────────
    status_message = pyqtSignal(str)
    # str: 状态说明文案（如“大律准石 14 < 基准 80，材料不足”）
