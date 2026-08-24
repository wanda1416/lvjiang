"""模拟装备创建/编辑对话框（MockEquipDialog）的词条硬约束测试

规则（不含首词条，首词条完全独立不受此约束）：
1. 词条 2-5 互不重复（游戏铁律：调律词条不会与已调出的重复），但允许与首词条相同。
2. 属攻类词条（四大基础属性攻击 + 会自动适配武学的"无相攻击"）最多 2 条。
3. 神力词条（增效类 + 武器类）最多 1 条。
4. 神力词条不能是转律产出的。

判定实现在 core/equip_validator.py，本对话框只决定「拦下」还是「放行并标记」：
以上四条组合类违规一律拦下；词条数值超上限只标记不拦（见 test_equip_validator.py）。

填写过程中完全不做约束（候选下拉不互相排除，可以随便选重复），只在点击
确认（_on_accept）保存时统一校验并拦截，不满足则弹窗提示、不关闭对话框。
"""

from lvjiang.apps.yysls.ui.loadout.equip.mock_dialog import MockEquipDialog


def _set_row(dlg: MockEquipDialog, row_1based: int, name: str, value: float = 1.0):
    """选中第 row_1based 行（1=首词条/宫）的词条名并填数值"""
    row = dlg._affix_rows[row_1based - 1]
    idx = row._combo_name.findData(name)
    assert idx >= 0, f"「{name}」不在第 {row_1based} 行候选里"
    row._combo_name.setCurrentIndex(idx)
    row._spin_value.setValue(value)


def _accept_and_capture_warning(dlg, monkeypatch):
    """点击确认，拦截 QMessageBox.warning，返回其提示文本（未弹窗则返回 None）"""
    import lvjiang.apps.yysls.ui.loadout.equip.mock_dialog as mod
    warned = []
    monkeypatch.setattr(
        mod.QMessageBox, "warning",
        lambda *args, **kwargs: warned.append(args[2] if len(args) > 2 else ""))
    dlg._on_accept()
    return warned[0] if warned else None


def _make_ring_dialog(qtbot) -> MockEquipDialog:
    """构造一个部位=环的对话框：环的属攻候选有 4 属性 × min/max 共 8 个，
    神力候选只有「全武学增效」1 个——足够触发数量上限。"""
    dlg = MockEquipDialog()
    qtbot.addWidget(dlg)
    dlg._combo_part.setCurrentText("环")
    dlg._edit_name.setText("测试装备")
    return dlg


class TestNoConstraintWhileFilling:
    """填写阶段：候选不互相排除，允许自由选重复"""

    def test_duplicate_attack_affix_selectable_across_rows(self, qtbot):
        dlg = _make_ring_dialog(qtbot)
        _set_row(dlg, 2, "最大鸣金攻击")
        _set_row(dlg, 3, "最大鸣金攻击")  # 与词条2重复，UI 层不应拦截
        assert dlg._affix_rows[1]._combo_name.currentData() == "最大鸣金攻击"
        assert dlg._affix_rows[2]._combo_name.currentData() == "最大鸣金攻击"

    def test_three_attack_affixes_selectable(self, qtbot):
        dlg = _make_ring_dialog(qtbot)
        _set_row(dlg, 2, "最大鸣金攻击")
        _set_row(dlg, 3, "最大牵丝攻击")
        _set_row(dlg, 4, "最大裂石攻击")
        assert [dlg._affix_rows[i]._combo_name.currentData() for i in (1, 2, 3)] == [
            "最大鸣金攻击", "最大牵丝攻击", "最大裂石攻击"]


class TestValidationOnSave:
    """保存阶段：违反任一规则则拦截并弹窗，不写入结果"""

    def test_more_than_two_attack_affixes_blocked(self, qtbot, monkeypatch):
        dlg = _make_ring_dialog(qtbot)
        _set_row(dlg, 2, "最大鸣金攻击")
        _set_row(dlg, 3, "最大牵丝攻击")
        _set_row(dlg, 4, "最大裂石攻击")

        message = _accept_and_capture_warning(dlg, monkeypatch)
        assert message is not None
        assert "属攻" in message
        assert dlg.get_result() is None

    def test_duplicate_attack_affix_blocked(self, qtbot, monkeypatch):
        dlg = _make_ring_dialog(qtbot)
        _set_row(dlg, 2, "最大鸣金攻击")
        _set_row(dlg, 3, "最大鸣金攻击")

        message = _accept_and_capture_warning(dlg, monkeypatch)
        assert message is not None
        assert "重复" in message
        assert dlg.get_result() is None

    def test_duplicate_non_attack_affix_blocked(self, qtbot, monkeypatch):
        dlg = _make_ring_dialog(qtbot)
        _set_row(dlg, 2, "会心率")
        _set_row(dlg, 3, "会心率")

        message = _accept_and_capture_warning(dlg, monkeypatch)
        assert message is not None
        assert "重复" in message
        assert dlg.get_result() is None

    def test_two_distinct_attack_affixes_allowed(self, qtbot, monkeypatch):
        dlg = _make_ring_dialog(qtbot)
        _set_row(dlg, 2, "最大鸣金攻击")
        _set_row(dlg, 3, "最大牵丝攻击")

        message = _accept_and_capture_warning(dlg, monkeypatch)
        assert message is None
        assert dlg.get_result() is not None

    def test_more_than_one_divine_affix_blocked(self, qtbot, monkeypatch):
        dlg = _make_ring_dialog(qtbot)
        _set_row(dlg, 2, "全武学增效")
        _set_row(dlg, 3, "全武学增效")  # 环唯一的神力候选只有这一个，重复选正好触发数量上限

        message = _accept_and_capture_warning(dlg, monkeypatch)
        assert message is not None
        assert "神力" in message
        assert dlg.get_result() is None

    def test_single_divine_affix_allowed(self, qtbot, monkeypatch):
        dlg = _make_ring_dialog(qtbot)
        _set_row(dlg, 2, "全武学增效")

        message = _accept_and_capture_warning(dlg, monkeypatch)
        assert message is None
        assert dlg.get_result() is not None

    def test_transferred_divine_affix_rejected(self, qtbot, monkeypatch):
        """转律不会产出神力词条：直接构造带 is_transferred 的 result 校验该规则。

        MockEquipDialog 当前 UI 本就不暴露"标记转律"的入口（_AffixRow.get_data()
        永远不写 is_transferred），因此实际填写路径天然满足这条规则；这里对
        _validate_affix_rules 本身做单元测试，覆盖将来数据里出现该字段的情况
        （例如从真实扫描装备的 JSON 导入覆盖模拟装备）。
        """
        dlg = _make_ring_dialog(qtbot)
        result = {
            "type": "环",
            "affix_2": {"name": "全武学增效", "value": 1.0, "is_transferred": True},
        }
        message = dlg._validate_affix_rules(result)
        assert message is not None
        assert "转律" in message

    def test_repeated_non_attack_affix_blocked(self, qtbot, monkeypatch):
        """铁律一：词条 2-5 互不重复，且不限于属攻类。

        最大外功攻击归属「外功类」，不受属攻/神力任何数量规则约束，
        只有通用重复规则能拦住它——这里是该规则的回归点。
        """
        dlg = _make_ring_dialog(qtbot)
        for row in (2, 3, 4, 5):
            _set_row(dlg, row, "最大外功攻击")

        message = _accept_and_capture_warning(dlg, monkeypatch)
        assert message is not None
        assert "重复" in message
        assert "最大外功攻击" in message
        assert dlg.get_result() is None

    def test_two_identical_non_attack_affixes_blocked(self, qtbot, monkeypatch):
        """只重复两条也要拦——不是"全部相同"才算违规。"""
        dlg = _make_ring_dialog(qtbot)
        _set_row(dlg, 2, "劲")
        _set_row(dlg, 3, "劲")
        _set_row(dlg, 4, "势")

        message = _accept_and_capture_warning(dlg, monkeypatch)
        assert message is not None
        assert "重复" in message
        assert dlg.get_result() is None

    def test_affix_same_as_first_allowed(self, qtbot, monkeypatch):
        """词条 2-5 允许与首词条相同：首词条是装备自带的初始词条，不由调律产出。"""
        dlg = _make_ring_dialog(qtbot)
        first_candidates = dlg._get_first_affix_names()
        shared = next((n for n in first_candidates
                       if dlg._affix_rows[1]._combo_name.findData(n) >= 0), None)
        assert shared, "环部位应存在既可作首词条、也在普通词条候选里的词条"
        _set_row(dlg, 1, shared)
        _set_row(dlg, 2, shared)   # 与首词条同名 —— 合法
        _set_row(dlg, 3, "劲")

        message = _accept_and_capture_warning(dlg, monkeypatch)
        assert message is None
        assert dlg.get_result() is not None

    def test_divine_count_reported_before_duplicate(self, qtbot, monkeypatch):
        """两条神力选了同一个时，报"神力最多 1 条"而不是笼统的"不能重复"。"""
        dlg = _make_ring_dialog(qtbot)
        _set_row(dlg, 2, "全武学增效")
        _set_row(dlg, 3, "全武学增效")

        message = _accept_and_capture_warning(dlg, monkeypatch)
        assert message is not None
        assert "神力" in message

    def test_live_warning_label_updates_without_saving(self, qtbot):
        """红字提示随选择实时刷新，不需要点确认，也不会弹窗/阻塞填写。"""
        dlg = _make_ring_dialog(qtbot)
        assert dlg._lbl_affix_warning.text() == ""

        _set_row(dlg, 2, "最大鸣金攻击")
        assert dlg._lbl_affix_warning.text() == ""  # 只有 1 条，还没冲突

        _set_row(dlg, 3, "最大鸣金攻击")  # 重复
        assert "重复" in dlg._lbl_affix_warning.text()

        _set_row(dlg, 3, "最大牵丝攻击")  # 改成不重复的
        assert dlg._lbl_affix_warning.text() == ""

    def test_live_warning_clears_when_switching_part_resets_rows(self, qtbot):
        """切换部位会静默重建词条候选（可能清空/改变已选词条），红字提示要跟着刷新。"""
        dlg = _make_ring_dialog(qtbot)
        _set_row(dlg, 2, "全武学增效")
        _set_row(dlg, 3, "全武学增效")
        assert "神力" in dlg._lbl_affix_warning.text()

        dlg._combo_part.setCurrentText("武器")
        dlg._combo_weapon_type.setCurrentText("剑")
        assert dlg._lbl_affix_warning.text() == ""

    def test_first_affix_not_constrained(self, qtbot, monkeypatch):
        """首词条完全独立，不受属攻/神力约束——这里用武器部位验证：
        首词条（宫）选中"外功攻击"这类首词条专属候选，与词条 2-5 的规则无关。"""
        dlg = MockEquipDialog()
        qtbot.addWidget(dlg)
        dlg._combo_part.setCurrentText("环")
        dlg._edit_name.setText("测试装备")
        first_candidates = dlg._get_first_affix_names()
        assert first_candidates, "环部位应有首词条候选"
        _set_row(dlg, 1, first_candidates[0])
        # 词条 2-5 全部留空，只有首词条——应正常通过校验
        message = _accept_and_capture_warning(dlg, monkeypatch)
        assert message is None
        assert dlg.get_result() is not None
