"""TuningDocWriter（调律说明文档写手）单元测试

覆盖：文档头内容与缺省口径、装备节、只写命中的规则（不适用/低评级
被过滤）、轮次与继续/结束原因、收尾评级过滤、中断收尾、逐次 flush。
全部写入 tmp_path，不触碰 logs/tuning/。
"""

import pytest

from lvjiang.apps.yysls.workflows.tuning_doc import (
    TuningDocWriter,
    format_affix,
)


class TestFormatAffix:
    def test_full(self):
        affix = {"name": "会心伤害", "value": 12, "unit": "%", "cap_pct": 85}
        assert format_affix(affix) == "会心伤害 12%（85%）"


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


    def test_all_ui_slots_are_summarized_as_all(self, writer):
        from lvjiang.apps.yysls.config.tune_slots import DEFAULT_SLOTS

        writer.start_run("u", [], list(DEFAULT_SLOTS), {})
        text = _read(writer)
        assert "- 调律部位：全部" in text
        assert "主武器、环" not in text


    def test_worthiness_filters_unmatched(self, writer):
        """只写命中 顶级/优秀 的规则；垃圾/跳过/不适用 均不写"""
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
        writer.food_strategy("首词条 92% >= 90% → 本轮添加 金狗粮")
        writer.tune_round(1, "金狗粮", "无视防御 8%（76%）")
        writer.round_decision("仍可达 顶级/优秀（血河），继续")
        writer.tune_round(2, "", "拆招 5%（40%）")
        writer.round_decision("新词条加入后不再可达 顶级/优秀，结束调律")
        text = _read(writer)
        assert "狗粮策略：首词条 92% >= 90% → 本轮添加 金狗粮" in text
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
        writer.finish_equipment(3, 5, "词条已满", judgement)
        text = _read(writer)
        assert "最终评级：血河：优秀（可转律）；素问：跳过（未实现）" in text
        assert "九灵" not in text
        assert "本件小结：共 3 轮，词条 5/5，结束原因：词条已满" in text

    def test_unfinished_equipment_has_no_final_rating(self, writer):
        judgement = {
            "a": {"name": "血河", "rating": "优秀", "skipped": False,
                  "not_applicable": False, "reasons": ["潜力预测"]},
        }
        writer.finish_equipment(2, 4, "无法继续调律", judgement)
        text = _read(writer)
        assert "最终评级：未评级（词条未满）" in text
        assert "血河：优秀" not in text


    def test_note_and_end_run_interrupted(self, writer):
        writer.note("已符合规则但未找到调律入口，跳过本件")
        writer.end_run(interrupted=True, tuned_count=3, total_rounds=11)
        text = _read(writer)
        assert "> 已符合规则但未找到调律入口，跳过本件" in text
        assert "## 运行结束" in text
        assert "（用户中断（F10））" in text
        assert "- 实际调律 3 件，共 11 轮" in text
