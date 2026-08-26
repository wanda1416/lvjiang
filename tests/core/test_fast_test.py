from __future__ import annotations

from types import SimpleNamespace

from scripts import fast_test


def _capture_commands(monkeypatch, returncode: int = 0) -> list[list[str]]:
    commands: list[list[str]] = []

    def fake_run(command: list[str], *, dry: bool) -> int:
        commands.append(command)
        return returncode

    monkeypatch.setattr(fast_test, "_run", fake_run)
    monkeypatch.setattr(fast_test, "_changed_python_files", lambda: [])
    return commands


def test_default_uses_testmon(monkeypatch) -> None:
    commands = _capture_commands(monkeypatch)
    monkeypatch.setattr(
        fast_test.importlib.util,
        "find_spec",
        lambda name: SimpleNamespace() if name == "testmon" else None,
    )

    assert fast_test.main([]) == 0
    assert commands == [
        [fast_test.sys.executable, "-m", "pytest", "tests/", "--testmon", "-x", "-q"]
    ]


def test_exact_node_id_bypasses_testmon(monkeypatch) -> None:
    commands = _capture_commands(monkeypatch)
    node_id = (
        "tests/core/test_config_resolver.py"
        "::TestModeDetection::test_env_forces_dev"
    )

    assert fast_test.main(["--no-lint", node_id]) == 0
    assert node_id in commands[0]
    assert "--testmon" not in commands[0]


def test_all_refreshes_testmon_database(monkeypatch) -> None:
    commands = _capture_commands(monkeypatch)

    assert fast_test.main(["--all", "--no-lint"]) == 0
    assert "--testmon" in commands[0]
    assert "--testmon-noselect" in commands[0]


def test_last_failed_mode_and_passthrough(monkeypatch) -> None:
    commands = _capture_commands(monkeypatch)

    assert fast_test.main(["--lf", "--no-lint", "--", "-vv", "--tb=short"]) == 0
    assert "--lf" in commands[0]
    assert "--lfnf=none" in commands[0]
    assert commands[0][-2:] == ["-vv", "--tb=short"]


def test_subprocess_failure_is_returned(monkeypatch) -> None:
    _capture_commands(monkeypatch, returncode=7)

    assert fast_test.main(["--no-lint", "tests/core/test_config_resolver.py"]) == 7


def test_missing_testmon_has_actionable_error(monkeypatch, capsys) -> None:
    commands = _capture_commands(monkeypatch)
    monkeypatch.setattr(fast_test.importlib.util, "find_spec", lambda name: None)

    assert fast_test.main(["--no-lint"]) == 2
    assert not commands
    assert "pip install -e '.[dev]'" in capsys.readouterr().err
