"""where confidence >= N 子句测试

验证：
1. 语法解析：where 子句正确解析为 WhereClause AST
2. scan + where：低置信度 OCR 结果被过滤
3. scan by + where：低置信度结果不参与匹配
4. find + where：低置信度结果不参与搜索
5. 变量阈值：where confidence >= $var 运行时解析
"""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from lvjiang.workflows.grammar import Literal, VarRef, WhereClause, parse_text
from lvjiang.workflows.grammar.ast_nodes import Find, Recognize, Scan


def _write_wf(tmp_path: Path, text: str) -> Path:
    wf = tmp_path / "t.wf"
    wf.write_text(text, encoding="utf-8")
    return wf


# ─── 语法解析测试 ─────────────────────────────────────────────


class TestWhereClauseParsing:
    """where 子句语法解析"""

    def test_scan_with_where(self):
        tree = parse_text('scan [s].[a] as $v where confidence >= 0.8\n')
        stmt = tree.body[0]
        assert isinstance(stmt, Scan)
        assert stmt.where is not None
        assert isinstance(stmt.where, WhereClause)
        assert isinstance(stmt.where.min_confidence, Literal)
        assert stmt.where.min_confidence.value == 0.8

    def test_scan_by_with_where(self):
        tree = parse_text('scan [s].[a] as $v by contains "text" where confidence >= 0.7\n')
        stmt = tree.body[0]
        assert isinstance(stmt, Scan)
        assert stmt.by is not None
        assert stmt.where is not None
        assert stmt.where.min_confidence.value == 0.7

    def test_recognize_with_where(self):
        tree = parse_text('recognize [s].[a] as $v where confidence >= 0.9\n')
        stmt = tree.body[0]
        assert isinstance(stmt, Recognize)
        assert stmt.where is not None
        assert stmt.where.min_confidence.value == 0.9

    def test_find_with_where(self):
        tree = parse_text('find as $v by contains "text" where confidence >= 0.6\n')
        stmt = tree.body[0]
        assert isinstance(stmt, Find)
        assert stmt.where is not None
        assert stmt.where.min_confidence.value == 0.6

    def test_find_area_with_where(self):
        tree = parse_text('find [s].[a] as $v by contains "text" where confidence >= 0.5\n')
        stmt = tree.body[0]
        assert isinstance(stmt, Find)
        assert stmt.where is not None
        assert stmt.where.min_confidence.value == 0.5

    def test_where_with_variable(self):
        tree = parse_text('scan [s].[a] as $v where confidence >= $threshold\n')
        stmt = tree.body[0]
        assert isinstance(stmt, Scan)
        assert stmt.where is not None
        assert isinstance(stmt.where.min_confidence, VarRef)
        assert stmt.where.min_confidence.name == "threshold"

    def test_scan_without_where(self):
        tree = parse_text('scan [s].[a] as $v\n')
        stmt = tree.body[0]
        assert isinstance(stmt, Scan)
        assert stmt.where is None

    def test_scan_panel_with_where(self):
        tree = parse_text('scan [s].[p] as $v where confidence >= 0.8\n')
        stmt = tree.body[0]
        assert isinstance(stmt, Scan)
        assert stmt.where is not None
        assert stmt.where.min_confidence.value == 0.8

    def test_recognize_panel_with_where(self):
        tree = parse_text('recognize [s].[p] as $v where confidence >= 0.8\n')
        stmt = tree.body[0]
        assert isinstance(stmt, Recognize)
        assert stmt.where is not None

    def test_scan_panel_cell_with_where(self):
        tree = parse_text('scan [s].[p][1][2] as $v where confidence >= 0.8\n')
        stmt = tree.body[0]
        assert isinstance(stmt, Scan)
        assert stmt.where is not None


# ─── 执行层测试：scan + where 过滤 ────────────────────────────


class TestWhereClauseExecution:
    """where 子句执行层过滤验证"""

    def test_scan_filters_low_confidence(self, tmp_path):
        """scan + where 过滤低置信度 OCR 结果"""
        from lvjiang.workflows.engine.core import WorkflowEngine

        capture = MagicMock()
        capture.get_capture_size.return_value = (100, 100)
        layout = MagicMock()
        layout.get_canvas.return_value = SimpleNamespace(
            x_ratio=0.0, y_ratio=0.0, w_ratio=1.0, h_ratio=1.0)
        region = SimpleNamespace(key="field_1")
        layout.get_scene_regions.return_value = [region]
        layout.get_scene_panels.return_value = []

        engine = WorkflowEngine(
            capture=capture, ocr=MagicMock(), input_ctrl=MagicMock(),
            layout=layout, input_sim=MagicMock(), delay_params={},
        )
        workflow = MagicMock()
        workflow.ocr_scene.return_value = {"field_1": "高置信文字"}
        engine._workflow = workflow

        wf = _write_wf(tmp_path, 'scan [s].[field_1] as $v where confidence >= 0.8\ncollect $v\n')
        output = engine.execute(wf)

        workflow.ocr_scene.assert_called_once_with(
            "s", ["field_1"], min_confidence=0.8,
        )
        assert output["v"] == {"field_1": "高置信文字"}

    def test_scan_without_where_passes_none(self, tmp_path):
        """scan 无 where 时 min_confidence=None"""
        from lvjiang.workflows.engine.core import WorkflowEngine

        capture = MagicMock()
        capture.get_capture_size.return_value = (100, 100)
        layout = MagicMock()
        layout.get_canvas.return_value = SimpleNamespace(
            x_ratio=0.0, y_ratio=0.0, w_ratio=1.0, h_ratio=1.0)
        region = SimpleNamespace(key="field_1")
        layout.get_scene_regions.return_value = [region]
        layout.get_scene_panels.return_value = []

        engine = WorkflowEngine(
            capture=capture, ocr=MagicMock(), input_ctrl=MagicMock(),
            layout=layout, input_sim=MagicMock(), delay_params={},
        )
        workflow = MagicMock()
        workflow.ocr_scene.return_value = {"field_1": "文字"}
        engine._workflow = workflow

        wf = _write_wf(tmp_path, 'scan [s].[field_1] as $v\ncollect $v\n')
        engine.execute(wf)

        workflow.ocr_scene.assert_called_once_with(
            "s", ["field_1"], min_confidence=None,
        )

    def test_scan_by_with_where_passes_min_confidence(self, tmp_path):
        """scan by + where 同时传递 min_confidence"""
        from lvjiang.workflows.engine.core import WorkflowEngine

        capture = MagicMock()
        capture.get_capture_size.return_value = (100, 100)
        layout = MagicMock()
        layout.get_canvas.return_value = SimpleNamespace(
            x_ratio=0.0, y_ratio=0.0, w_ratio=1.0, h_ratio=1.0)
        region = SimpleNamespace(key="field_1")
        layout.get_scene_regions.return_value = [region]
        layout.get_scene_panels.return_value = []

        engine = WorkflowEngine(
            capture=capture, ocr=MagicMock(), input_ctrl=MagicMock(),
            layout=layout, input_sim=MagicMock(), delay_params={},
        )
        workflow = MagicMock()
        workflow.ocr_scene_by.return_value = "field_1"
        engine._workflow = workflow

        wf = _write_wf(tmp_path,
            'scan [s].[field_1] as $v by contains "文字" where confidence >= 0.7\n'
            'collect $v\n'
        )
        engine.execute(wf)

        workflow.ocr_scene_by.assert_called_once_with(
            "s", ["field_1"], "文字", "contains",
            min_confidence=0.7,
        )

    def test_where_with_variable_threshold(self, tmp_path):
        """where confidence >= $var 运行时解析变量"""
        from lvjiang.workflows.engine.core import WorkflowEngine

        capture = MagicMock()
        capture.get_capture_size.return_value = (100, 100)
        layout = MagicMock()
        layout.get_canvas.return_value = SimpleNamespace(
            x_ratio=0.0, y_ratio=0.0, w_ratio=1.0, h_ratio=1.0)
        region = SimpleNamespace(key="field_1")
        layout.get_scene_regions.return_value = [region]
        layout.get_scene_panels.return_value = []

        engine = WorkflowEngine(
            capture=capture, ocr=MagicMock(), input_ctrl=MagicMock(),
            layout=layout, input_sim=MagicMock(), delay_params={},
        )
        workflow = MagicMock()
        workflow.ocr_scene.return_value = {"field_1": "文字"}
        engine._workflow = workflow

        wf = _write_wf(tmp_path,
            'eval $threshold = 0.9\n'
            'scan [s].[field_1] as $v where confidence >= $threshold\n'
            'collect $v\n'
        )
        engine.execute(wf)

        workflow.ocr_scene.assert_called_once_with(
            "s", ["field_1"], min_confidence=0.9,
        )

    def test_where_with_undefined_variable_returns_none(self, tmp_path):
        """where confidence >= $undefined — 变量未定义时不崩溃，min_confidence 为 None"""
        from lvjiang.workflows.engine.core import WorkflowEngine

        capture = MagicMock()
        capture.get_capture_size.return_value = (100, 100)
        layout = MagicMock()
        layout.get_canvas.return_value = SimpleNamespace(
            x_ratio=0.0, y_ratio=0.0, w_ratio=1.0, h_ratio=1.0)
        region = SimpleNamespace(key="field_1")
        layout.get_scene_regions.return_value = [region]
        layout.get_scene_panels.return_value = []

        engine = WorkflowEngine(
            capture=capture, ocr=MagicMock(), input_ctrl=MagicMock(),
            layout=layout, input_sim=MagicMock(), delay_params={},
        )
        workflow = MagicMock()
        workflow.ocr_scene.return_value = {"field_1": "文字"}
        engine._workflow = workflow

        wf = _write_wf(tmp_path,
            'scan [s].[field_1] as $v where confidence >= $undefined_var\n'
            'collect $v\n'
        )
        engine.execute(wf)

        workflow.ocr_scene.assert_called_once_with(
            "s", ["field_1"], min_confidence=None,
        )
