"""毕业率计算 Mix-in —— 后台线程调度与上下文快照。

职责：
- GraduationContext 数据类：流派/方案/基础属性公共快照
- _GraduationTask：线程池任务，调用 get_graduation_calculator 计算
- CombatGraduationMixin：调度、防抖、结果接收
"""
from __future__ import annotations

from dataclasses import dataclass

from loguru import logger
from PyQt6.QtCore import QObject, QRunnable, QThreadPool, pyqtSignal

from ....core.combat.combat_attrs import CombatAttributes


@dataclass(frozen=True)
class GraduationContext:
    """供其他组件读取的毕业率计算上下文快照。"""

    school: str
    scheme: str
    base_attrs: CombatAttributes
    gongjue: str = ""  # 当前弓玦类型（"会意"/"精准"/"会心"/""）


class _GraduationSignals(QObject):
    finished = pyqtSignal(int, str, str, str, object, object)


class _GraduationTask(QRunnable):
    """在线程池执行耗时公式，并携带请求快照返回结果。"""

    def __init__(
        self,
        generation: int,
        user_name: str,
        school: str,
        scheme: str,
        attrs: CombatAttributes,
    ) -> None:
        super().__init__()
        self.generation = generation
        self.user_name = user_name
        self.school = school
        self.scheme = scheme
        self.attrs = attrs
        self.signals = _GraduationSignals()

    def run(self) -> None:
        try:
            from ....core.graduation import get_graduation_calculator

            calculator = get_graduation_calculator(self.school, self.scheme)
            result = calculator.calculate(self.attrs) if calculator else None
            error = None
        except Exception as exc:  # UI worker boundary
            result = None
            error = exc
        self.signals.finished.emit(
            self.generation,
            self.user_name,
            self.school,
            self.scheme,
            result,
            error,
        )


class CombatGraduationMixin:
    """毕业率计算 Mix-in。

    需要主类提供：
    - self._host（宿主 API）
    - self._graduation_generation（int，请求代次）
    - self._pending_graduation（tuple | None，待处理请求）
    - self._graduation_timer（QTimer，防抖定时器）
    - self._combo_scheme（QComboBox，方案下拉）
    - self._get_current_school() -> str
    - self._get_current_gongjue() -> str
    - self._get_base_attrs() -> CombatAttributes

    计算结果通过 host.graduation_updated 信号向上传递，
    由 LoadoutPanel 统一存储和分发。
    """

    def _schedule_graduation(self, combat_attrs: CombatAttributes) -> None:
        """延迟提交毕业率计算；新请求会使旧结果自动失效。"""
        self._graduation_generation += 1
        generation = self._graduation_generation
        self._graduation_timer.stop()
        school = self._get_current_school()
        scheme = self._combo_scheme.currentText()
        user_name = self._host.active_user_name() or ""
        if not school or not scheme or not user_name:
            self._pending_graduation = None
            self._host.graduation_updated.emit(None)
            return

        attrs_snapshot = CombatAttributes.from_dict(combat_attrs.to_dict())
        self._pending_graduation = (
            generation, user_name, school, scheme, attrs_snapshot,
        )
        self._graduation_timer.start()

    def _start_graduation_task(self) -> None:
        request = self._pending_graduation
        if request is None:
            return
        generation, user_name, school, scheme, attrs = request
        if generation != self._graduation_generation:
            return
        task = _GraduationTask(generation, user_name, school, scheme, attrs)
        task.signals.finished.connect(self._on_graduation_finished)
        pool = QThreadPool.globalInstance()
        if pool is not None:
            pool.start(task)

    def _on_graduation_finished(
        self,
        generation: int,
        user_name: str,
        school: str,
        scheme: str,
        result,
        error,
    ) -> None:
        """仅接收仍与当前用户和配置一致的后台计算结果。"""
        if (
            generation != self._graduation_generation
            or user_name != (self._host.active_user_name() or "")
            or school != self._get_current_school()
            or scheme != self._combo_scheme.currentText()
        ):
            return
        if error is not None:
            logger.error(f"毕业率计算失败: {error}")
            self._host.graduation_updated.emit(None)
            return
        # 结果通过信号向上传递给 LoadoutPanel
        self._host.graduation_updated.emit(result)

    def get_graduation_context(self) -> GraduationContext | None:
        """返回当前流派/方案及不含装备的基础属性公共快照。

        注意：base_attrs 不含弓玦属性，弓玦类型单独传递，
        由调用方（如最优组合搜索对话框）按需重算。
        """
        school = self._get_current_school()
        scheme = self._combo_scheme.currentText()
        if not school or not scheme:
            return None
        base_attrs = self._get_base_attrs()
        gongjue = self._get_current_gongjue()
        return GraduationContext(
            school=school,
            scheme=scheme,
            base_attrs=CombatAttributes.from_dict(base_attrs.to_dict()),
            gongjue=gongjue,
        )
