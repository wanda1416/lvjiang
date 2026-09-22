"""毕业率分析对话框的入口装配：从当前用户构造计算上下文并打开对话框。

这是 controller 工作，不属于装备页：装备页只提供它掌握的东西（当前用户、
筛选阈值、卡片显示参数、写回转律目标的能力），流派/模型/基础属性/假设副本
都在这里拼装。对话框本身（``graduation_analysis``）与各页（``optimal_combo``、
``affix_analysis_pages``）不依赖装备页。
"""
from __future__ import annotations

import copy
from collections.abc import Callable
from typing import Any

from loguru import logger
from PyQt6.QtWidgets import QMessageBox, QWidget

from .....i18n import tr


def analysis_dependencies():
    """集中解析词条分析依赖，便于在不打开对话框时验证导入路径。"""
    from ...config import get_game_config
    from ...core.combat.equipment import EquipmentInventory
    from ...core.graduation.affix_impact import (
        analyze_affix_impacts,
        analyze_combined_affix_replacements,
    )
    from ...core.graduation.context import PlanContextError, PlanScoringContext
    from ...core.graduation.transmute_optimizer import (
        TransmuteSearchRequest,
        optimize_transmutes,
    )
    from .affix_analysis_pages import AffixAnalysisPages

    return (
        get_game_config,
        EquipmentInventory,
        PlanScoringContext,
        PlanContextError,
        analyze_affix_impacts,
        analyze_combined_affix_replacements,
        AffixAnalysisPages,
        TransmuteSearchRequest,
        optimize_transmutes,
    )


class GraduationAnalysisLauncher:
    """打开「毕业率分析」对话框所需的一切协作者都显式注入。

    - ``assumptions_source``：返回备战方案面板当前假设（对话框取其副本）。
    - ``level_threshold`` / ``affix_filter``：装备页当前筛选，最优组合页沿用。
    - ``display_params``：卡片显示参数。
    - ``apply_transmute``：把转律建议写回公共装备，返回是否已写入。
    """

    def __init__(
        self,
        host: Any,
        parent: QWidget,
        *,
        assumptions_source: Callable[[], Any],
        level_threshold: Callable[[], int],
        affix_filter: Callable[[], str],
        display_params: Callable[[], dict],
        apply_transmute: Callable[..., bool],
    ) -> None:
        self._host = host
        self._parent = parent
        self._assumptions_source = assumptions_source
        self._level_threshold = level_threshold
        self._affix_filter = affix_filter
        self._display_params = display_params
        self._apply_transmute = apply_transmute
        self._caches: dict[tuple[str, str], Any] = {}

    def cache_for(self, user_name: str, plan_id: str):
        """按“用户 + 备战方案”隔离的上次计算结果。"""
        from .graduation_analysis import AnalysisCache

        return self._caches.setdefault((user_name, plan_id), AnalysisCache())

    def open(self, initial_tab: int) -> None:
        """构建共享上下文（流派、模型、基础属性、假设副本）并打开分析对话框。"""
        from .graduation_analysis import AssumptionBar, GraduationAnalysisDialog
        from .optimal_combo import OptimalComboPage

        parent = self._parent
        user_name = self._host.active_user_name()
        if not user_name:
            QMessageBox.warning(parent, tr("提示"), tr("没有激活的用户"))
            return

        try:
            (
                get_game_config,
                EquipmentInventory,
                PlanScoringContext,
                PlanContextError,
                analyze_affix_impacts,
                analyze_combined_affix_replacements,
                AffixAnalysisPages,
                TransmuteSearchRequest,
                optimize_transmutes,
            ) = analysis_dependencies()

            game_config = get_game_config()
            inventory = EquipmentInventory(user_name)
            equipped = inventory.equipped
            plan = inventory.active_plan
            plan_id = inventory.active_plan_id
            from lvjiang.core.user_config import (
                get_graduation_analysis_settings,
                set_graduation_analysis_settings,
            )
            users_dir = inventory._repo.users_dir
            analysis_settings = get_graduation_analysis_settings(
                user_name, plan_id, users_dir)
            # 方案 → 计算上下文（流派、模型、基础属性含弓玦）只构造一次
            try:
                scoring = PlanScoringContext.from_plan(
                    plan, game_config=game_config)
            except PlanContextError as exc:
                QMessageBox.warning(
                    parent, tr("配置不完整"),
                    tr("无法打开毕业率分析：{reason}。请检查当前备战方案的战斗属性设置。")
                    .format(reason=exc.reason))
                return
            calculator = scoring.calculator
            base_attrs = scoring.base_attrs
            school = scoring.school
            school_pool = tuple(game_config.get_transmute_pool(school))

            # 假设栏：备战方案面板假设的副本，关闭即弃
            bar = AssumptionBar(
                self._assumptions_source(),
                season_level=game_config.current_equip_level(),
            )

            def transmute_runner(stop_check, assumptions):
                return optimize_transmutes(TransmuteSearchRequest(
                    equipped=copy.deepcopy(equipped),
                    calculator=calculator,
                    base_attrs=base_attrs,
                    school=school,
                    game_config=game_config,
                    stop_check=stop_check,
                    school_pool=school_pool,
                    full_chengyin=assumptions.full_chengyin,
                    full_dingyin=assumptions.full_dingyin,
                    full_level=assumptions.full_level,
                    playstyle=assumptions.playstyle,
                ))

            def apply_handler(result) -> bool:
                return self._apply_transmute(
                    result, user_name=user_name, plan_id=plan_id)

            affix_pages = AffixAnalysisPages(
                school,
                scoring.scheme,
                equipped=equipped,
                report_provider=lambda: analyze_affix_impacts(
                    equipped,
                    calculator,
                    base_attrs,
                    school,
                    game_config=game_config,
                ),
                joint_analyzer=lambda slots: analyze_combined_affix_replacements(
                    equipped,
                    (),
                    slots,
                    calculator,
                    base_attrs,
                    school,
                    game_config=game_config,
                ),
                transmute_runner=transmute_runner,
                apply_handler=apply_handler,
                assumptions_provider=bar.value,
                display_params=self._display_params(),
            )
            optimal_page = OptimalComboPage(
                self._host, school, scoring.scheme,
                scoring.base_attrs_without_gongjue,
                level_threshold=self._level_threshold(),
                affix_filter=self._affix_filter(),
                gongjue=scoring.gongjue,
                playstyle=plan.playstyle,
                main_martial_art=plan.main_martial_art,
                sub_martial_art=plan.sub_martial_art,
                assumptions_provider=bar.value,
                analysis_settings=analysis_settings,
                settings_changed=lambda value: set_graduation_analysis_settings(
                    user_name, plan_id, value, users_dir),
            )
            dialog = GraduationAnalysisDialog(
                parent,
                school=school,
                scheme=scoring.scheme,
                plan_name=plan.name,
                assumption_bar=bar,
                optimal_page=optimal_page,
                affix_pages=affix_pages,
                cache=self.cache_for(user_name, plan_id),
                initial_tab=initial_tab,
            )
            dialog.exec()
        except Exception as exc:
            logger.error(f"毕业率分析打开失败: {exc}")
            QMessageBox.critical(parent, tr("分析失败"), str(exc))
