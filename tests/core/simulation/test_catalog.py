"""Missing recordings and unsupported tasks cannot be folded into green coverage."""

import hashlib
import json

from lvjiang.core.simulation.bundle import bundle_workflow
from lvjiang.core.simulation.catalog import discover, run_discovered
from lvjiang.core.simulation.demo import make_demo


def test_discovery_and_parallel_coverage_keep_missing_and_invalid_separate(tmp_path):
    fixtures = tmp_path / "fixtures"
    case = fixtures / "observed"
    make_demo(case)
    workflows = tmp_path / "workflows"
    workflows.mkdir()
    library = workflows / "lib/shared.wf"
    library.parent.mkdir()
    library.write_text("def helper()\nreturn 7\nend\n", encoding="utf-8")
    task = case / "task.wf"
    task.write_text(
        'import "lib/shared.wf"\n' + task.read_text(encoding="utf-8"), encoding="utf-8"
    )
    bundle_workflow(task, case / "bundle", roots=[workflows])
    spec = json.loads((case / "scenario.json").read_text(encoding="utf-8"))
    spec.update(
        workflow_sha256=hashlib.sha256(task.read_bytes()).hexdigest(),
        workflow_bundle="bundle",
    )
    (case / "scenario.json").write_text(json.dumps(spec), encoding="utf-8")
    (workflows / "observed.wf").write_bytes(task.read_bytes())
    (workflows / "missing.wf").write_text("return 0\n", encoding="utf-8")
    (workflows / "unsafe.wf").write_text("eval save()\nreturn 0\n", encoding="utf-8")
    (workflows / "library.wf").write_text(
        "def local_proc()\nreturn 0\nend\n", encoding="utf-8"
    )
    data = discover(workflows, fixtures)
    catalog = tmp_path / "catalog.json"
    catalog.write_text(json.dumps(data), encoding="utf-8")
    report = run_discovered(catalog, tmp_path / "results", jobs=2)
    assert not report["all_required_passed"]
    assert report["counts"]["passed"] == 1
    assert report["counts"]["missing_recording"] == 2
    assert report["capability_counts"]["unsupported"] == 1
    assert all(r["workflow"] != "library.wf" for r in report["rows"])
    broken = fixtures / "broken"
    make_demo(broken)
    (broken / "start.png").write_bytes(b"broken")
    assert discover(workflows, fixtures)["invalid_fixtures"]

    library.write_text("def helper()\nreturn 8\nend\n", encoding="utf-8")
    current = discover(workflows, fixtures)
    stale = next(
        row for row in current["workflows"] if row["workflow"] == "observed.wf"
    )
    assert not stale["fixtures"] and stale["rejected_fixtures"]
    catalog.write_text(json.dumps(current), encoding="utf-8")
    report = run_discovered(catalog, tmp_path / "stale-results")
    assert report["counts"]["failed"] == 1 and not report["all_required_passed"]

    library.unlink()
    current = discover(workflows, fixtures)
    catalog.write_text(json.dumps(current), encoding="utf-8")
    report = run_discovered(catalog, tmp_path / "missing-library")
    assert report["counts"]["unsupported"] == 1 and report["counts"]["passed"] == 0
