"""_CreatePlayStyleDialog 预填（initial_values）测试

重点覆盖一个容易踩的坑：角色面板会展示"非本流派"的属攻数值（装备词条
带的其他流派属攻，如裂石流派角色装备恰好有牵丝词条），basic 属性里不
可能出现非本属的属攻——OCR 解析结果里会带上全部四门武学的分项数值，
但对话框只应该按当前流派把 __min_attr__ 等占位符解析到的那一个具体
字段（如 min_lieshi/max_lieshi）读出来填表，其余三门流派的具体字段
（min_mingjin/min_qiansi/min_pozhu 等）必须原样保持 CombatAttributes()
默认值 0.0，绝不能被 OCR 结果"顺手"带进面板值——否则后续
`base = panel - equip_base - equip_attrs - gongjue` 反推基础值时，
装备提供的非本属数值会被当成"面板 0 - 装备 N"算出一个错误的负数基础值
并有被误保存的风险。
"""

from lvjiang.apps.yysls.core.combat.combat_attrs import CombatAttributes
from lvjiang.apps.yysls.ui.loadout.combat.play_style_dialog import (
    _CreatePlayStyleDialog,
)


class TestResolveInitialValues:
    def test_maps_generic_current_keys_to_concrete_school_fields(self):
        resolved = _CreatePlayStyleDialog._resolve_initial_values(
            {
                "min_attr_current": 366.0,
                "max_attr_current": 527.0,
                "attr_pen_current": 10.3,
                "attr_bonus_current": 4.1,
            },
            "裂石",
        )
        assert resolved["min_lieshi"] == 366.0
        assert resolved["max_lieshi"] == 527.0
        assert resolved["lieshi_pen"] == 10.3
        assert resolved["lieshi_bonus"] == 4.1

    def test_existing_concrete_value_not_overwritten(self):
        """OCR 恰好也给出了具体字段的值时，不应被通用兜底 key 覆盖"""
        resolved = _CreatePlayStyleDialog._resolve_initial_values(
            {"min_lieshi": 999.0, "min_attr_current": 366.0}, "裂石",
        )
        assert resolved["min_lieshi"] == 999.0

    def test_no_school_attr_keeps_values_as_is(self):
        values = {"min_attr_current": 366.0}
        resolved = _CreatePlayStyleDialog._resolve_initial_values(values, None)
        assert resolved == values

    def test_unknown_school_attr_keeps_values_as_is(self):
        values = {"min_attr_current": 366.0}
        resolved = _CreatePlayStyleDialog._resolve_initial_values(values, "不存在的流派")
        assert resolved == values


class TestOffSchoolAttackDataNeverLeaksIntoPanelAttrs:
    """核心安全性回归：验证角色是裂石流派时，OCR 带回来的鸣金/牵丝/破竹
    具体字段数值绝不会出现在 get_panel_attrs() 结果里——对话框根本没有
    为这些字段创建输入框，自然也不会被 _save_play_style 读取/持久化。
    """

    def test_only_current_school_fields_are_populated(self, qtbot):
        # 模拟 to_role_base_attrs 的输出：四门武学的具体字段全部都在，
        # 加上当前流派（裂石）的通用兜底 key
        prefill = {
            "min_mingjin": 0.0, "max_mingjin": 0.0,
            "min_lieshi": 570.0, "max_lieshi": 1224.0,
            "min_qiansi": 63.0, "max_qiansi": 63.0,   # 装备带的非本属残留值
            "min_pozhu": 65.0, "max_pozhu": 65.0,      # 同上
            "min_wuxiang": 66.0, "max_wuxiang": 199.0,
            "min_attr_current": 570.0, "max_attr_current": 1224.0,
        }
        dlg = _CreatePlayStyleDialog(school_attr="裂石", initial_values=prefill)
        qtbot.addWidget(dlg)

        panel_attrs = dlg.get_panel_attrs()

        # 当前流派（裂石）字段正确读入
        assert panel_attrs.min_lieshi == 570.0
        assert panel_attrs.max_lieshi == 1224.0

        # 无相攻击是 PLAY_STYLE_FIELD_GROUPS 里唯一一个"非占位符、始终存在"
        # 的字段（不随当前流派门槛，任意流派都能有无相攻击），合法填入
        assert panel_attrs.min_wuxiang == 66.0
        assert panel_attrs.max_wuxiang == 199.0

        # 其余三门"互斥"流派（鸣金/牵丝/破竹）的具体字段必须保持默认值
        # 0.0，不能被 OCR 数据"顺手"带入面板值——否则
        # base = panel(0) - equip(装备提供的非本属数值) 会算出一个错误的
        # 负数基础值
        default = CombatAttributes()
        assert panel_attrs.min_mingjin == default.min_mingjin == 0.0
        assert panel_attrs.max_mingjin == default.max_mingjin == 0.0
        assert panel_attrs.min_qiansi == default.min_qiansi == 0.0
        assert panel_attrs.max_qiansi == default.max_qiansi == 0.0
        assert panel_attrs.min_pozhu == default.min_pozhu == 0.0
        assert panel_attrs.max_pozhu == default.max_pozhu == 0.0

    def test_reverse_derivation_does_not_produce_off_school_negative_base(self, qtbot):
        """完整走一遍 panel - equip 反推，确认非本属字段的"负数基础值"
        只是 __sub__ 的瞬时计算结果，永远不会被读出来保存。"""
        prefill = {
            "min_lieshi": 570.0, "max_lieshi": 1224.0,
            "min_qiansi": 63.0, "max_qiansi": 63.0,
        }
        dlg = _CreatePlayStyleDialog(school_attr="裂石", initial_values=prefill)
        qtbot.addWidget(dlg)
        panel_attrs = dlg.get_panel_attrs()

        # 装备恰好在牵丝上提供了 59（角色实际不用牵丝流派）
        equip_attrs = CombatAttributes()
        equip_attrs.min_qiansi = 59.0

        base_attrs = panel_attrs - equip_attrs
        # __sub__ 确实会算出一个"错误"的负数（0 - 59），这本身符合预期——
        # 关键是它不能被读出来persist
        assert base_attrs.min_qiansi == -59.0

        # 模拟 _save_play_style 的写入逻辑：只从 PLAY_STYLE_FIELD_GROUPS
        # 按当前流派占位符解析出的字段名读取，min_qiansi 不在其中
        from lvjiang.apps.yysls.core.combat.combat_attrs import (
            PLAY_STYLE_FIELD_GROUPS,
            SCHOOL_ATTR_FIELD_MAP,
        )

        attr_map = SCHOOL_ATTR_FIELD_MAP["裂石"]
        saved_field_names = set()
        for _, fields in PLAY_STYLE_FIELD_GROUPS:
            for fn, _, _ in fields:
                if fn == "__min_attr__":
                    fn = attr_map["min_attr"]
                elif fn == "__max_attr__":
                    fn = attr_map["max_attr"]
                elif fn == "__attr_pen__":
                    fn = attr_map["attr_pen"]
                elif fn == "__attr_bonus__":
                    fn = attr_map["attr_bonus"]
                if fn and not fn.startswith("__"):
                    saved_field_names.add(fn)

        assert "min_qiansi" not in saved_field_names
        assert "min_lieshi" in saved_field_names
