"""上机前预检：设备端布局 × 暴露脚本 的静态引用校验（只解析，不执行）

设备端跑 .wf 时，引擎在进入执行阶段前会做两道静态校验：命名等待参数是否
已定义、脚本引用的场景/区域/坐标点/方向/面板是否已在当前布局绑定。这两道
过去只能等上机时才触发 —— 而实机失败的代价是游戏已经被点到别处去了。

本脚本在 PC 上按设备端同一条路径（plugins.ensure_loaded → _default_layout_name
→ _load_layout）装配引擎，再调 `WorkflowEngine.validate_only()` —— 它只跑解析 +
两道校验就返回，不产生任何点击，且判据与真正执行时共用同一份。

与 `tests/workflows/test_system_wf_refs_gate.py` 的分工：那边是 CI 门禁，固定只比
`config/system` 层（可复现）；这边走设备端真实发现路径 + session 活动布局，
能反映本机 `config/local` 影子文件带来的差异，上机前跑一次。

用法：
    .venv\\Scripts\\python.exe -X utf8 scripts/manual-tests/preflight_device_workflows.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from lvjiang.config import load_user_config  # noqa: E402
from lvjiang.core.config_resolver import get_resolver  # noqa: E402
from lvjiang.core.ondevice.plugins import ensure_loaded  # noqa: E402
from lvjiang.core.ondevice.workflow_runner import (  # noqa: E402
    _default_layout_name,
    _load_layout,
)
from lvjiang.workflows.discovery import list_exposed_scripts  # noqa: E402
from lvjiang.workflows.engine import WorkflowEngine  # noqa: E402


def _make_validator(layout, user_config) -> WorkflowEngine:
    """装配「只校验」的引擎：后端全为 None，validate_only 不触碰它们"""
    return WorkflowEngine(
        capture=None, ocr=None, input_ctrl=None, layout=layout,
        input_sim=user_config.input_sim,
        delay_params=user_config.delay_params,
        window_left=0, window_top=0,
    )


def main() -> int:
    ensure_loaded()  # 注册插件的工作流实现与内置函数（设备端同一步）

    layout_name = _default_layout_name()
    layout = _load_layout(layout_name)
    user_config = load_user_config()
    print(f"设备端布局: {layout_name}")

    items = list_exposed_scripts()
    failed = 0
    for item in items:
        label = f"{item['id']} ({item['name']})"
        if item["class"] or not item["wf_file"]:
            # 类实现的脚本（auto_tuning / single_tuning）在 Python 代码里引用坐标，
            # 静态引用收集器只认 DSL 语法，这里覆盖不到，需实机或单测验证
            print(f"  [跳过] {label} — 类实现，非 DSL 脚本")
            continue
        wf_path = get_resolver().resolve_read(f"workflows/{item['wf_file']}")
        if wf_path is None:
            failed += 1
            print(f"  [失败] {label} — 找不到脚本文件 {item['wf_file']}")
            continue
        try:
            _make_validator(layout, user_config).validate_only(wf_path)
            print(f"  [通过] {label}")
        except Exception as e:
            failed += 1
            print(f"  [失败] {label}\n{e}")

    print(f"\n合计 {len(items)} 项，静态校验失败 {failed} 项")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
