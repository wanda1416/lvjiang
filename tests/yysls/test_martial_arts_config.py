"""武学配置：武器与属性是武学的固有属性，流派只引用。"""
from __future__ import annotations

import json
from pathlib import Path

from lvjiang.apps.yysls.config import get_game_config

_ROOT = Path(__file__).resolve().parents[2]
_GRADUATION_DIR = _ROOT / "config/system/yysls/graduation"


def test_registry_is_loaded():
    arts = get_game_config().get_martial_arts()
    assert len(arts) >= 22
    assert arts["无名剑法"] == {"weapon": "剑", "attr": "鸣金"}


def test_weapon_and_attr_are_derived_from_the_art():
    g = get_game_config()
    assert g.get_martial_art_weapon("无名剑法") == "剑"
    assert g.get_martial_art_attr("无名剑法") == "鸣金"
    # 未登记不抛异常，返回空串——调用方据此降级而不是崩
    assert g.get_martial_art_weapon("查无此学") == ""
    assert g.get_martial_art_attr("查无此学") == ""


def test_lookup_by_weapon():
    assert get_game_config().get_martial_arts_by_weapon("剑") == [
        "无名剑法", "积矩九剑"]


def test_shipped_schools_are_consistent_with_the_registry():
    """随包配置不能自相矛盾。

    拆分之前 weapon 和 martial_art 各录各的，写成「武器=枪 + 武学=无名剑法」
    也存得下来，然后毕业率按枪算、词条按剑法找，全程静默。这条把它钉住。
    """
    assert get_game_config().check_school_weapon_consistency() == []


class TestPlaystyleRegistry:
    """玩法提到公共层：规则只引用，不再各自定义。"""

    def test_registry_is_loaded(self):
        styles = get_game_config().get_playstyles()
        assert len(styles) == 14
        assert styles["纯唐"]["main_weapon"] == "横刀"
        assert styles["纯唐"]["main_damage"] == "横刀武学增伤"
        assert styles["纯唐"]["school"] == "裂石·钧"
        assert styles["双切"]["main_weapon"] == "横刀"
        assert styles["双切"]["sub_weapon"] == "陌刀"
        assert styles["双切"]["sub_damage"] == "陌刀武学增伤"
        assert styles["火拳"]["output_dingyin"] == "外功穿透"
        assert styles["火拳"]["defense_dingyin"] == ""
        assert all(
            style["output_dingyin"] == "外功穿透"
            for style in styles.values()
        )
        assert styles["火拳"]["all_skill_requirement"] == "不需要"
        assert styles["纯奶"]["unit_requirement"] == "玩家"
        assert styles["飞天玉"]["qishu_requirement"] == "不需要"
        assert not any(
            style["qishu_requirement"] == "群体"
            for style in styles.values()
        )

    def test_switch_stays_with_the_rule_not_the_playstyle(self):
        """开关控制的是非武器增伤这类判定口径，属于规则的事。

        同一个玩法在不同规则下可以绑不同开关甚至不绑，所以它不能写进公共
        玩法定义。提取时如果把它一起搬走，判定会静默改变。
        """
        import glob
        from pathlib import Path

        import yaml

        assert all("switch" not in cfg
                   for cfg in get_game_config().get_playstyles().values())

        bound = {}
        for path in glob.glob("config/system/yysls/tuning_rules/*.yaml"):
            raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
            bound.update(raw.get("playstyle_switches") or {})
        assert bound == {}

    def test_rules_only_reference_playstyles(self):
        """规则文件里不能再内嵌玩法定义——那正是重复的来源。"""
        import glob
        from pathlib import Path

        import yaml

        registry = get_game_config().get_playstyles()
        for path in glob.glob("config/system/yysls/tuning_rules/*.yaml"):
            raw = yaml.safe_load(
                Path(path).read_text(encoding="utf-8")) or {}
            refs = raw.get("playstyles")
            if refs is None:
                continue
            assert isinstance(refs, list), f"{path} 仍在内嵌定义玩法"
            for name in refs:
                assert name in registry, f"{path} 引用了未登记的玩法 {name}"

    def test_lookup_by_arts_is_unordered(self):
        """主副只是顺序标签，判别式是「要谁的增伤」。

        选了 (斩雪刀法, 十方破阵) 时纯唐和双切都该列出来由用户挑，而不是按
        主副顺序只给一个。
        """
        g = get_game_config()
        styles = g.get_playstyles()
        # 用一个临时注册表验证匹配语义
        g._playstyles = {
            "纯唐": {**styles["纯唐"], "arts": ["斩雪刀法", "十方破阵"]},
            "双切": {**styles["双切"], "arts": ["十方破阵", "斩雪刀法"]},
            "无关": {**styles["无名"], "arts": ["无名剑法", "无名枪法"]},
        }
        try:
            assert g.get_playstyles_for_arts(
                ["斩雪刀法", "十方破阵"]) == ["双切", "纯唐"]
            assert g.get_playstyles_for_arts(
                ["十方破阵", "斩雪刀法"]) == ["双切", "纯唐"]
            assert g.get_playstyles_for_arts(["斩雪刀法"]) == []
        finally:
            g.reload()

    def test_bound_playstyles_follow_their_school_identity(self):
        """绑定流派的属性、武学和派生武器必须与流派完全一致。"""
        g = get_game_config()
        schools = g.get_schools()
        for name, style in g.get_playstyles().items():
            school = style["school"]
            assert school in schools, name
            cfg = schools[school]
            assert style["attr"] == cfg["attr"], name
            assert style["arts"] == [
                cfg["main"]["martial_art"],
                cfg["sub"]["martial_art"],
            ], name
            assert style["main_weapon"] == cfg["main"]["weapon"], name
            assert style["sub_weapon"] == cfg["sub"]["weapon"], name

    def test_school_affix_group_does_not_depend_on_art_prefixes(self):
        names = get_game_config().get_affix_names_in_group(
            "指定技能增效", "破竹·樽")
        assert "悬身断水·浓醺技能增伤" in names
        assert "断水双诀·轻击增伤" in names

    def test_damage_and_defense_targets_follow_graduation_baselines(self):
        """内置输出玩法以毕业率表的满值属性为真源。

        武器增伤满值大于 0 才是该流派的必要词条；防具定音同理从
        流派的「指定技能增效」分组中取值。基础方案 JSON 是
        ``data/excel`` 毕业率计算表的版本化转换结果。
        """
        g = get_game_config()
        exceptions = {"纯奶", "纯唐", "飞天玉"}

        for name, style in g.get_playstyles().items():
            if name in exceptions or not style.get("school"):
                continue

            scheme_path = _GRADUATION_DIR / f'{style["school"]}_基础方案.json'
            scheme = json.loads(scheme_path.read_text(encoding="utf-8"))
            extra = scheme["baseline_attrs"]["extra_attrs"]

            for side in ("main", "sub"):
                affix = g.get_weapon_wuxue_affix(style[f"{side}_weapon"])
                expected = affix if float(extra.get(affix, 0)) > 0 else ""
                assert style.get(f"{side}_damage", "") == expected, name

            defense = [
                affix for affix in g.get_affix_names_in_group(
                    "指定技能增效", style["school"])
                if float(extra.get(affix, 0)) > 0
            ]
            assert len(defense) <= 1, name
            assert style.get("defense_dingyin", "") == (
                defense[0] if defense else ""), name

    def test_non_maximum_damage_playstyles_keep_manual_targets(self):
        """低输出/治疗玩法不得被流派最大化伤害表反向覆盖。"""
        styles = get_game_config().get_playstyles()
        assert {
            name: {
                "main_damage": styles[name].get("main_damage", ""),
                "sub_damage": styles[name].get("sub_damage", ""),
                "defense_dingyin": styles[name].get("defense_dingyin", ""),
            }
            for name in ("纯奶", "纯唐", "飞天玉")
        } == {
            "纯奶": {
                "main_damage": "扇武学增效",
                "sub_damage": "",
                "defense_dingyin": "明川药典治疗技增疗",
            },
            "纯唐": {
                "main_damage": "横刀武学增伤",
                "sub_damage": "",
                "defense_dingyin": "斩雪刀法轻重击派生技增伤",
            },
            "飞天玉": {
                "main_damage": "伞武学增效",
                "sub_damage": "",
                "defense_dingyin": "九重春色高频弹道增伤",
            },
        }

    def test_dingyin_target_splits_by_affix_category_parts(self):
        """输出/防御的划分沿用词条类别自己的 _parts，不另建分组。"""
        g = get_game_config()
        assert g.get_affix_category_parts("指定技能增效") == [
            "冠胄", "胸甲", "胫甲", "腕甲"]


def test_no_wrong_character_for_the_rope_dart_arts():
    """粟子游尘 / 粟子行云 的「粟」是游戏合法名，形近字 U+6817 不是。

    两个字只差一撇，肉眼审查发现不了；而武学名是毕业率 JSON、遥测词表和词条池
    之间的连接键，写错一处就静默断链——查不到武学、算不出定音，却没有任何地方
    会报错。此前它在 02-school-system.md 里活了很久。

    这里用码位而不是字面量，否则本文件会触发自己的检查。
    """
    import subprocess

    wrong = chr(0x6817)
    tracked = subprocess.run(
        ["git", "ls-files"], capture_output=True, text=True, check=True,
    ).stdout.split()
    offenders = []
    for path in tracked:
        candidate = Path(path)
        if candidate.suffix in {".png", ".jpg", ".xlsx", ".ico", ".zip"}:
            continue
        try:
            text = candidate.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if wrong in text:
            offenders.append(path)
    assert offenders == [], f"应为「粟」而非 U+6817: {offenders}"
