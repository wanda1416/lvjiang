"""项目分组代码统计脚本测试。"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/project_stats.py"
SPEC = importlib.util.spec_from_file_location("project_stats", SCRIPT)
assert SPEC and SPEC.loader
project_stats = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = project_stats
SPEC.loader.exec_module(project_stats)


def _scc_payload(code: int = 10) -> str:
    return json.dumps(
        [
            {
                "Name": "Python",
                "Count": 2,
                "Lines": code + 5,
                "Code": code,
                "Comment": 3,
                "Blank": 2,
                "Complexity": 4,
                "Files": [
                    {
                        "Location": r"src\lvjiang\large.py",
                        "Language": "Python",
                        "Lines": code + 5,
                        "Code": code,
                        "Comment": 3,
                        "Blank": 2,
                        "Complexity": 4,
                    }
                ],
            }
        ]
    )


def test_parse_scc_json_sums_languages():
    raw = json.dumps(
        [
            {"Name": "Python", "Count": 2, "Lines": 15, "Code": 10},
            {"Name": "Kotlin", "Count": 1, "Lines": 8, "Code": 6},
        ]
    )

    total, languages, files = project_stats.parse_scc_json(raw)

    assert total.files == 3
    assert total.lines == 23
    assert total.code == 16
    assert set(languages) == {"Python", "Kotlin"}
    assert files == []


def test_collect_group_invokes_scc_with_project_relative_paths(monkeypatch):
    calls = []

    class Completed:
        returncode = 0
        stdout = _scc_payload()
        stderr = ""

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return Completed()

    monkeypatch.setattr(project_stats.subprocess, "run", fake_run)
    group = project_stats.Group("sample", "样本", ("src", "tests"))

    result = project_stats.collect_group("/tools/scc", group)

    assert calls[0][0] == [
        "/tools/scc", "--by-file", "--format", "json", "src", "tests"
    ]
    assert calls[0][1]["cwd"] == project_stats.ROOT
    assert result.total.code == 10
    assert result.files[0].path == "src/lvjiang/large.py"


def test_main_reports_android_and_repository_totals(monkeypatch, capsys):
    monkeypatch.setattr(project_stats.shutil, "which", lambda _name: "/tools/scc")
    monkeypatch.setattr(
        project_stats,
        "collect_group",
        lambda _scc, group: project_stats.Result(
            group,
            project_stats.Counts(files=1, lines=15, code=10, comments=3, blanks=2),
            {"Python": project_stats.Counts(files=1, lines=15, code=10)},
            [
                project_stats.SourceFile(
                    f"{group.key}/large.py",
                    "Python",
                    project_stats.Counts(lines=15, code=10, comments=3, blanks=2),
                )
            ],
        ),
    )

    assert project_stats.main([]) == 0

    output = capsys.readouterr().out
    assert "Android 合计" in output
    assert "生产代码合计" in output
    assert "含测试仓库合计" in output
    assert "Top 4 文件" in output
    assert "src/large.py" in output


def test_main_without_scc_returns_actionable_error(monkeypatch, capsys):
    monkeypatch.setattr(project_stats.shutil, "which", lambda _name: None)

    assert project_stats.main([]) == 2

    assert "--scc" in capsys.readouterr().err
