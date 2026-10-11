"""隔离进程中的候选配置校验；不运行工作流，不连接设备。"""
from __future__ import annotations

import json
from fnmatch import fnmatchcase
from pathlib import Path

import yaml

from ..update_bundle import read_tree


def validate_documents(root: Path, patterns: tuple[str, ...] = ()) -> tuple[str, ...]:
    """只解释现有消费者认识的文档；其它文件完整保留。"""
    from ...apps import load_config_policies
    from ...workflows.grammar.parser.api import parse_text
    from .versioning import spec_for

    load_config_policies()
    checked = []
    for name, data in read_tree(root).items():
        path = Path(name)
        known = (name in {"app.yaml", "scenes.yaml", "layouts.yaml", "ocr.yaml"}
                 or spec_for(name) is not None
                 or (name.startswith("references/") and len(path.parts) == 2 and path.suffix == ".yaml")
                 or any(fnmatchcase(name, pattern) for pattern in patterns))
        if not known:
            continue
        text = data.decode("utf-8")
        if name.startswith("workflows/") and path.suffix == ".wf":
            parse_text(text)
        elif path.suffix == ".json":
            json.loads(text)
        else:
            document = yaml.safe_load(text)
            if not isinstance(document, dict):
                raise ValueError(f"配置文档必须是映射: {name}")
        checked.append(name)
    return tuple(checked)


def validate_runtime(config: Path, app_ids: tuple[str, ...]) -> None:
    """必须在独立进程调用：把所有全局路径重定向到临时候选，再使用真实引擎校验。"""
    from ... import constants
    from . import resolver

    constants.PROJECT_ROOT = config.parent
    constants.CONFIG_DIR = config
    constants.SESSION_CONFIG_DIR = config / "session"
    constants.SESSION_PATH = config / "session/session.json"
    constants.BATCH_CONFIG_PATH = config / "session/batch.json"
    constants.USERS_DIR = config / "session/users"
    constants.AVATARS_DIR = config / "session/avatars"
    constants.OUTPUT_DIR = config / "session/output"
    resolver.SYSTEM_CONFIG_DIR = config / "system"
    resolver.LOCAL_CONFIG_DIR = config / "local"
    resolver._resolver = resolver.ConfigResolver(
        system_dir=config / "system", local_dir=config / "local",
        dev_mode=False)

    from ...apps import load_app, register_hooks
    from .update_activation import validate_candidate

    for app_id in app_ids:
        register_hooks(load_app(app_id))
    validate_candidate(config / "system", config / "local", app_ids)


def main() -> None:
    import argparse

    from ..._version import __version__
    from ..update_bundle import series

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--app-series", required=True)
    parser.add_argument("--app", action="append", default=[])
    args = parser.parse_args()
    if series(__version__) != args.app_series:
        raise ValueError("校验器代码版本与目标正式系列不一致")
    validate_runtime(args.config, tuple(args.app))


if __name__ == "__main__":
    main()
