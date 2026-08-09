"""整面板 scan/recognize 与 $var.[行].[列] 数字 key 访问

scan [scene].[key] 的 key 命中 panel（而非 region）时分派为整面板逐格识别，
结果为行列嵌套 dict（key 为 1-based 字符串）；配套语法 $var.[1].[2] 静态
数字 key 与 $var.$r 动态数字 key（for 循环 int 归一化为 "1" 字符串）取值。
"""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import cv2
import numpy as np

from lvjiang.workflows.engine import WorkflowEngine
from lvjiang.workflows.grammar import Eval, FieldAccess, Literal, VarRef, parse_text
from lvjiang.workflows.workflow_references import collect_refs

DATA_DIR = Path(__file__).parent / "data"

# ─── 语法：数字字面量 key ─────────────────────────────────

def test_parse_numeric_bracket_field_access():
    """$bags.[1].[2] → 链式 FieldAccess，key 为 Literal("1")/Literal("2")"""
    p = parse_text('eval $x = $bags.[1].[2]\n')
    n = p.body[0]
    assert isinstance(n, Eval)
    fa = n.func_args[0]
    assert isinstance(fa, FieldAccess)
    assert isinstance(fa.field_name, Literal) and fa.field_name.value == "2"
    assert isinstance(fa.root, FieldAccess)
    assert isinstance(fa.root.field_name, Literal) and fa.root.field_name.value == "1"
    assert isinstance(fa.root.root, VarRef) and fa.root.root.name == "bags"


def test_parse_numeric_bracket_in_condition():
    """条件语境同样可用：if $bags.[1].[2] contains "背包" """
    p = parse_text('if $bags.[1].[2] contains "背包"\n    log "hit"\nend\n')
    assert p.body  # 解析通过即可


# ─── 静态检查：单 key 放宽为 区域/面板 ────────────────────

def test_single_field_scan_kind_is_scan():
    refs = collect_refs(parse_text('scan [s].[actions] as $x\n').body, {})
    assert [(r.key, r.kind) for r in refs] == [("actions", "scan")]


def test_multi_field_scan_kind_stays_region():
    refs = collect_refs(parse_text('scan [s].[f1, f2] as $x\n').body, {})
    assert {(r.key, r.kind) for r in refs} == {("f1", "region"), ("f2", "region")}


# ─── 引擎：整面板扫描 ─────────────────────────────────────

class _FakeCal:
    """等分 2×2 网格的对齐结果替身"""
    n_rows = 2
    n_cols = 2
    total_slots = 4

    @staticmethod
    def slot_bounds(r, c):
        return (c * 0.5, r * 0.5, (c + 1) * 0.5, (r + 1) * 0.5)

    @staticmethod
    def slot_center(r, c):
        return (c * 0.5 + 0.25, r * 0.5 + 0.25)


def _make_engine():
    """最小引擎：场景 s 只绑一个 2×2 panel actions，无 region"""
    capture = MagicMock()
    capture.get_capture_size.return_value = (100, 100)
    capture.capture.return_value = np.zeros((100, 100, 3), dtype=np.uint8)
    layout = MagicMock()
    layout.get_canvas.return_value = SimpleNamespace(
        x_ratio=0.0, y_ratio=0.0, w_ratio=1.0, h_ratio=1.0)
    panel_obj = SimpleNamespace(
        key="actions", rows=2, cols=2,
        x_ratio=0.0, y_ratio=0.0, w_ratio=1.0, h_ratio=1.0)
    layout.get_scene_regions.return_value = []
    layout.get_scene_points.return_value = []
    layout.get_scene_arrows.return_value = []
    layout.get_scene_panels.side_effect = lambda k: (
        [panel_obj] if k == "s" else [])
    ocr = MagicMock()
    counter = iter(range(1, 100))
    ocr.recognize.side_effect = lambda img: [
        SimpleNamespace(text=f"t{next(counter)}")]
    engine = WorkflowEngine(
        capture=capture, ocr=ocr, input_ctrl=MagicMock(),
        layout=layout, input_sim=MagicMock(), delay_params={},
    )
    # 预置对齐结果，绕开真实 detect_grid
    engine._panel_alignments[("s", "actions")] = _FakeCal()
    return engine


def _write_wf(tmp_path, text: str):
    wf = tmp_path / "t.wf"
    wf.write_text(text, encoding="utf-8")
    return wf


def test_whole_panel_scan_nested_result(tmp_path):
    """scan [s].[actions] → {"1": {"1": t1, "2": t2}, "2": {...}}（行优先）"""
    wf = _write_wf(tmp_path, (
        'scan [s].[actions] as $bags\n'
        'collect $bags\n'
    ))
    output = _make_engine().execute(wf)
    assert output["bags"] == {
        "1": {"1": "t1", "2": "t2"},
        "2": {"1": "t3", "2": "t4"},
    }


def test_whole_panel_scan_bracket_and_dynamic_access(tmp_path):
    """$bags.[1].[2] 静态数字 key 与 $bags.$r.$c 动态 int key 取同一格"""
    wf = _write_wf(tmp_path, (
        'scan [s].[actions] as $bags\n'
        'eval $c12 = $bags.[1].[2]\n'
        'collect $c12\n'
        'eval $r = 1\n'
        'eval $c = 2\n'
        'eval $dyn = $bags.$r.$c\n'
        'collect $dyn\n'
    ))
    output = _make_engine().execute(wf)
    assert output["c12"] == "t2"
    # eval $r = 1 存的是 float 1.0，归一化为 "1" 后命中
    assert output["dyn"] == "t2"


def test_whole_panel_scan_for_loop_access(tmp_path):
    """for 循环变量（int）逐格遍历，数字 key 归一化生效"""
    wf = _write_wf(tmp_path, (
        'scan [s].[actions] as $bags\n'
        'eval $joined = ""\n'
        'for r in [1...2]\n'
        '    for c in [1...2]\n'
        '        eval $joined = concat($joined, $bags.$r.$c, ",")\n'
        '    end\n'
        'end\n'
        'collect $joined\n'
    ))
    output = _make_engine().execute(wf)
    assert output["joined"] == "t1,t2,t3,t4,"


def test_whole_panel_scan_with_by_clause_returns_position(tmp_path):
    """整面板 + by 返回首个命中的行列位置 {row, col}，未命中返回 {}"""
    wf = _write_wf(tmp_path, (
        'scan [s].[actions] as $pos by contains "t2"\n'
        'collect $pos\n'
    ))
    output = _make_engine().execute(wf)
    # t2 在 1 行 2 列
    assert output["pos"] == {"row": 1, "col": 2}


def test_whole_panel_scan_with_by_clause_no_match(tmp_path):
    """整面板 + by 未命中返回空 dict"""
    wf = _write_wf(tmp_path, (
        'scan [s].[actions] as $pos by contains "不存在"\n'
        'collect $pos\n'
    ))
    output = _make_engine().execute(wf)
    assert output["pos"] == {}


# ─── 动态 panel 引用：[scene].$panel ───────────────────

def test_whole_panel_scan_with_dynamic_panel_key(tmp_path):
    """scan [s].$pk — panel 名由变量解析，结果与静态引用一致"""
    wf = _write_wf(tmp_path, (
        'eval $pk = "actions"\n'
        'scan [s].$pk as $bags\n'
        'collect $bags\n'
    ))
    output = _make_engine().execute(wf)
    assert output["bags"] == {
        "1": {"1": "t1", "2": "t2"},
        "2": {"1": "t3", "2": "t4"},
    }


def test_click_cell_with_dynamic_panel_key(tmp_path):
    """click [s].$pk[1][1] — panel 名由变量解析后查对齐缓存点击格子中心"""
    wf = _write_wf(tmp_path, (
        'eval $pk = "actions"\n'
        'click [s].$pk[1][1]\n'
    ))
    engine = _make_engine()
    engine._input_sim.click_random_offset = 0
    engine.execute(wf)
    x, y = engine._input.click_screen.call_args.args[:2]
    # 100×100 画布上 2×2 等分，第 1 行第 1 列格子中心 (25, 25)
    assert (x, y) == (25, 25)


# ─── 单格 [r][c]：key 过滤，结果为该格文本 ──────────────

def test_single_cell_scan_returns_plain_text(tmp_path):
    """scan [s].[actions][1][2] → 该格文本 str，不再是 {"r1c2": text}"""
    wf = _write_wf(tmp_path, (
        'scan [s].[actions][1][2] as $bags\n'
        'collect $bags\n'
    ))
    output = _make_engine().execute(wf)
    assert output["bags"] == "t1"  # 单格只识别一次，计数器从 t1 开始


def test_single_cell_scan_matches_whole_panel_cell(tmp_path):
    """单格结果与整面板 $bags.[r].[c] 取值格式统一（同为纯文本）"""
    wf = _write_wf(tmp_path, (
        'scan [s].[actions] as $whole\n'
        'eval $from_whole = $whole.[1].[2]\n'
        'scan [s].[actions][1][2] as $single\n'
        'collect $from_whole\n'
        'collect $single\n'
    ))
    output = _make_engine().execute(wf)
    # 两次识别文本不同（计数器递增），但都是纯 str 而非 dict
    assert output["from_whole"] == "t2"
    assert isinstance(output["single"], str)


def test_single_cell_recognize_returns_plain_type(tmp_path):
    """recognize [s].[actions][1][1] → 材料类型名 str，不再是 {"r1c1": type}"""
    wf = _write_wf(tmp_path, (
        'recognize [s].[actions][1][1] as $mat\n'
        'collect $mat\n'
    ))
    engine = _make_engine()
    workflow = MagicMock()
    workflow.material_recognizer.recognize.return_value = SimpleNamespace(type="大律准石")
    engine._workflow = workflow
    output = engine.execute(wf)
    assert output["mat"] == "大律准石"


def test_whole_panel_recognize_nested_result(tmp_path):
    """recognize [s].[actions] → 嵌套 dict，value 为材料类型名"""
    wf = _write_wf(tmp_path, (
        'recognize [s].[actions] as $mats\n'
        'collect $mats\n'
    ))
    engine = _make_engine()
    # recognize 走 workflow.material_recognizer，注入替身
    workflow = MagicMock()
    counter = iter(range(1, 100))
    workflow.material_recognizer.recognize.side_effect = lambda img, group=None: (
        SimpleNamespace(type=f"m{next(counter)}"))
    engine._workflow = workflow
    output = engine.execute(wf)
    assert output["mats"] == {
        "1": {"1": "m1", "2": "m2"},
        "2": {"1": "m3", "2": "m4"},
    }


def test_region_key_still_goes_region_path(tmp_path):
    """key 命中 region 时保持既有区域 OCR 语义，不误分派"""
    capture = MagicMock()
    capture.get_capture_size.return_value = (100, 100)
    layout = MagicMock()
    layout.get_canvas.return_value = SimpleNamespace(
        x_ratio=0.0, y_ratio=0.0, w_ratio=1.0, h_ratio=1.0)
    region = SimpleNamespace(key="title")
    layout.get_scene_regions.return_value = [region]
    layout.get_scene_points.return_value = []
    layout.get_scene_arrows.return_value = []
    layout.get_scene_panels.return_value = []
    engine = WorkflowEngine(
        capture=capture, ocr=MagicMock(), input_ctrl=MagicMock(),
        layout=layout, input_sim=MagicMock(), delay_params={},
    )
    workflow = MagicMock()
    workflow.ocr_scene.return_value = {"title": "背包"}
    engine._workflow = workflow
    wf = _write_wf(tmp_path, 'scan [s].[title] as $x\ncollect $x\n')
    output = engine.execute(wf)
    workflow.ocr_scene.assert_called_once_with("s", ["title"], min_confidence=None)
    assert output["x"] == {"title": "背包"}


# ─── 实测图片：材料识别集成测试 ─────────────────────────────

class TestRealImageRecognition:
    """使用实测图片验证整面板识别流程"""

    def test_image1_row2_col1_is_cai_gouliang(self):
        """image1.png 第2行第1列应为彩狗粮"""
        from lvjiang.apps.yysls.core.material_recognizer import MaterialRecognizer
        from lvjiang.core.ocr import OCREngine
        from lvjiang.workflows.align import detect_grid

        img_path = DATA_DIR / "image1.png"
        img = cv2.imread(str(img_path))
        assert img is not None, f"无法读取图片: {img_path}"

        # 检测网格
        cal = detect_grid(img, expected_rows=5, expected_cols=6)
        assert cal is not None, "未检测到网格"
        assert cal.n_rows == 5
        assert cal.n_cols == 6

        # 提取第2行第1列的 slot 图片（0-indexed: row=1, col=0）
        h, w = img.shape[:2]
        x1, y1, x2, y2 = cal.slot_bounds(1, 0)
        slot_img = img[int(y1 * h):int(y2 * h), int(x1 * w):int(x2 * w)]

        # 材料识别
        ocr = OCREngine()
        recognizer = MaterialRecognizer(ocr)
        result = recognizer.recognize(slot_img, group="调律材料")

        assert result.type == "彩狗粮", f"期望彩狗粮，实际识别为 {result.type!r}"

    def test_image1_row2_col2_is_jin_gouliang(self):
        """image1.png 第2行第2列应为金狗粮"""
        from lvjiang.apps.yysls.core.material_recognizer import MaterialRecognizer
        from lvjiang.core.ocr import OCREngine
        from lvjiang.workflows.align import detect_grid

        img_path = DATA_DIR / "image1.png"
        img = cv2.imread(str(img_path))
        assert img is not None, f"无法读取图片: {img_path}"

        # 检测网格
        cal = detect_grid(img, expected_rows=5, expected_cols=6)
        assert cal is not None, "未检测到网格"

        # 提取第2行第2列的 slot 图片（0-indexed: row=1, col=1）
        h, w = img.shape[:2]
        x1, y1, x2, y2 = cal.slot_bounds(1, 1)
        slot_img = img[int(y1 * h):int(y2 * h), int(x1 * w):int(x2 * w)]

        # 材料识别
        ocr = OCREngine()
        recognizer = MaterialRecognizer(ocr)
        result = recognizer.recognize(slot_img, group="调律材料")

        assert result.type == "金狗粮", f"期望金狗粮，实际识别为 {result.type!r}"
