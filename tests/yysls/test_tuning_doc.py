"""TuningDocWriter（调律说明文档写手）单元测试

覆盖：文档头内容与缺省口径、装备节、只写命中的流派（不适用/低评级
被过滤）、轮次与继续/结束原因、收尾评级过滤、中断收尾、逐次 flush。
全部写入 tmp_path，不触碰 logs/tuning/。
"""

import pytest

from src.apps.yysls.workflows.tuning_doc import (
    TuningDocWriter,
    format_affix,
)


class TestFormatAffix:
    def test_full(self):
        affix = {"name": "会心伤害", "value": 12, "unit": "%", "cap_pct": 85}
        assert format_affix(affix) == "会心伤害 12%（85%）"

    def test_plain_value_no_cap(self):
        assert format_affix({"name": "攻击", "value": 300}) == "攻击 300"

    def test_missing_name_and_value(self):
        assert format_affix({}) == "未知词条"


@pytest.fixture
def writer(tmp_path):
    w = TuningDocWriter("小明", doc_dir=tmp_path)
    yield w
    w.close()


def _read(w: TuningDocWriter) -> str:
    return w.path.read_text(encoding="utf-8")


class TestTuningDocWriter:
    def test_filename_pattern(self, writer, tmp_path):
        assert writer.path.parent == tmp_path
        assert writer.path.name.startswith("调律说明_小明_")
        assert writer.path.suffix == ".md"

    def test_header(self, writer):
        writer.start_run("小明", ["血河（武器规则：长枪·破甲）", "素问"],
                         ["main_weapon", "head"], keep_pvp=False)
        text = _read(writer)
        assert text.startswith("# 调律说明 — ")
        assert "- 操作用户：小明" in text
        assert "- 启用流派：血河（武器规则：长枪·破甲）、素问" in text
        assert "- 保留PVP词条：否" in text
        assert "- 调律部位：主武器、头部" in text

    def test_header_defaults(self, writer):
        """空流派 → 全部流派；keep_pvp=True → 是；slot 中文映射"""
        writer.start_run("u", [], ["ring"], keep_pvp=True)
        text = _read(writer)
        assert "- 启用流派：全部流派（默认配置）" in text
        assert "- 保留PVP词条：是" in text
        assert "- 调律部位：环佩" in text

    def test_equipment_section(self, writer):
        equip = {
            "name": "无极棍", "type": "长枪", "level": 125, "quality": "gold",
            "affix_1": {"name": "会心伤害", "value": 12, "unit": "%",
                        "cap_pct": 85},
            "affix_2": {"name": "攻击", "value": 300, "cap_pct": 92},
        }
        writer.start_equipment(1, equip)
        text = _read(writer)
        assert "## 1. 无极棍 · 长枪（125级 金色）" in text
        assert "进入调律时词条（2/5）：" in text
        assert "- 会心伤害 12%（85%）" in text
        assert "- 攻击 300（92%）" in text

    def test_equipment_section_unknown_quality(self, writer):
        writer.start_equipment(2, {"name": "残剑", "type": "剑"})
        text = _read(writer)
        assert "## 2. 残剑 · 剑（品阶未知）" in text
        assert "进入调律时词条（0/5）：" in text

    def test_worthiness_filters_unmatched(self, writer):
        """只写命中 顶级/优秀 的流派；垃圾/跳过/不适用 均不写"""
        results = {
            "a": {"name": "血河", "rating": "顶级", "skipped": False,
                  "not_applicable": False, "reasons": ["词条匹配", "武器匹配"]},
            "b": {"name": "素问", "rating": "垃圾", "skipped": False,
                  "not_applicable": False, "reasons": ["词条不符"]},
            "c": {"name": "铁衣", "rating": "", "skipped": True,
                  "not_applicable": False, "reasons": ["未实现"]},
            "d": {"name": "九灵", "rating": "顶级", "skipped": False,
                  "not_applicable": True, "reasons": ["部位不适用"]},
        }
        writer.worthiness_matched(results)
        text = _read(writer)
        assert "符合以下规则，开始调律：" in text
        assert "- 血河：顶级（词条匹配；武器匹配）" in text
        assert "素问" not in text
        assert "铁衣" not in text
        assert "九灵" not in text

    def test_rounds_and_decision(self, writer):
        writer.food_strategy("首词条 92% >= 90% → 每轮添加 金狗粮")
        writer.tune_round(1, "金狗粮", "无视防御 8%（76%）")
        writer.round_decision("仍可达 顶级/优秀（血河），继续")
        writer.tune_round(2, "", "拆招 5%（40%）")
        writer.round_decision("新词条加入后不再可达 顶级/优秀，结束调律")
        text = _read(writer)
        assert "狗粮策略：首词条 92% >= 90% → 每轮添加 金狗粮" in text
        assert "第 1 轮：添加 金狗粮 + 一键添加律准石 → 新词条「无视防御 8%（76%）」" in text
        assert "  → 仍可达 顶级/优秀（血河），继续" in text
        # 无狗粮轮次不出现"添加  +"式的残缺措辞
        assert "第 2 轮：一键添加律准石 → 新词条「拆招 5%（40%）」" in text

    def test_finish_equipment(self, writer):
        judgement = {
            "a": {"name": "血河", "rating": "优秀", "skipped": False,
                  "not_applicable": False, "reasons": ["可转律"]},
            "b": {"name": "素问", "rating": "", "skipped": True,
                  "not_applicable": False, "reasons": ["未实现"]},
            "c": {"name": "九灵", "rating": "顶级", "skipped": False,
                  "not_applicable": True, "reasons": ["部位不适用"]},
        }
        writer.finish_equipment(2, 4, "判定不再可达顶级/优秀", judgement)
        text = _read(writer)
        assert "最终评级：血河：优秀（可转律）；素问：跳过（未实现）" in text
        assert "九灵" not in text
        assert "本件小结：共 2 轮，词条 4/5，结束原因：判定不再可达顶级/优秀" in text

    def test_finish_equipment_no_conclusion(self, writer):
        writer.finish_equipment(0, 2, "无法继续调律", {})
        assert "最终评级：无有效结论" in _read(writer)

    def test_note_and_end_run_interrupted(self, writer):
        writer.note("已符合规则但未找到调律入口，跳过本件")
        writer.end_run(interrupted=True, tuned_count=3, total_rounds=11)
        text = _read(writer)
        assert "> 已符合规则但未找到调律入口，跳过本件" in text
        assert "## 运行结束" in text
        assert "（用户中断（F10））" in text
        assert "- 实际调律 3 件，共 11 轮" in text

    def test_end_run_normal(self, writer):
        writer.end_run(interrupted=False, tuned_count=0, total_rounds=0)
        text = _read(writer)
        assert "（正常完成）" in text
        assert "- 实际调律 0 件，共 0 轮" in text

    def test_flush_immediate(self, writer):
        """未 close 即可从磁盘读到已写内容（中断/崩溃不丢）"""
        writer.start_run("小明", [], ["head"], keep_pvp=False)
        assert "- 操作用户：小明" in _read(writer)
