"""正式/测试发布共用门禁；只读源码与 Git，输出冻结的发布元数据。"""
from __future__ import annotations

import argparse
import ast
import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from lvjiang.core.versioning import application_version  # noqa: E402


def git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=root, check=True, capture_output=True,
                            text=True, encoding="utf-8")
    return result.stdout.strip()


def validate_release(root: Path, tag: str, *, test_only: bool = False) -> dict[str, str]:
    version = application_version(tag)
    if len(version.release) != 3:
        raise ValueError("发布标签必须为 X.Y.Z、X.Y.ZbN 或 X.Y.ZrcN")
    if test_only and not version.is_prerelease:
        raise ValueError("手动测试发布入口只允许 bN/rcN，正式版必须使用标签发布入口")
    with (root / "pyproject.toml").open("rb") as handle:
        declared = tomllib.load(handle)["project"]["version"]
    source = ast.parse((root / "src/lvjiang/_version.py").read_text(encoding="utf-8"))
    injected = next((ast.literal_eval(node.value) for node in source.body if isinstance(node, ast.Assign)
                     and any(isinstance(target, ast.Name) and target.id == "__version__" for target in node.targets)), None)
    gradle = (root / "android/app/build.gradle.kts").read_text(encoding="utf-8")
    name = re.search(r'\bversionName\s*=\s*"([^"]+)"', gradle)
    code = re.search(r"\bversionCode\s*=\s*(\d+)", gradle)
    if declared != tag or injected != tag or name is None or name.group(1) != tag:
        raise ValueError("标签、pyproject.toml、_version.py 与 Android versionName 必须完全一致")
    if code is None or int(code.group(1)) <= 0:
        raise ValueError("Android versionCode 必须为正整数")
    notes = f"docs/50-releases/v{tag}.md"
    body = (root / notes).read_text(encoding="utf-8")
    title = next((line[2:].strip() for line in body.splitlines() if line.startswith("# ")), "")
    if not re.match(rf"^v{re.escape(tag)}(?:\s|$)", title):
        raise ValueError(f"发布说明首个一级标题必须以 v{tag} 开头")
    head = git(root, "rev-parse", "HEAD")
    if git(root, "rev-parse", f"refs/tags/{tag}^{{commit}}") != head:
        raise ValueError("构建源码不是指定标签的提交")
    if not version.is_prerelease:
        git(root, "fetch", "origin", "master", "--no-tags")
        git(root, "merge-base", "--is-ancestor", head, "origin/master")
    # 测试包也必须能被后续包正常升级；按所有应用标签的最大版本码检查，不按名称排序。
    for previous in git(root, "tag", "--list").splitlines():
        if previous == tag:
            continue
        try:
            application_version(previous)
        except ValueError:
            continue
        try:
            old = git(root, "show", f"{previous}:android/app/build.gradle.kts")
        except subprocess.CalledProcessError:
            continue  # 早于 Android 应用的标签没有该文件。
        previous_code = re.search(r"\bversionCode\s*=\s*(\d+)", old)
        if previous_code and int(code.group(1)) <= int(previous_code.group(1)):
            raise ValueError(f"Android versionCode 必须超过已存在标签 {previous} 的 {previous_code.group(1)}")
    return {"version": tag, "notes": notes, "title": title,
            "prerelease": str(version.is_prerelease).lower()}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--test-only", action="store_true")
    parser.add_argument("--github-output", type=Path)
    args = parser.parse_args()
    result = validate_release(ROOT, args.tag, test_only=args.test_only)
    if args.github_output:
        with args.github_output.open("a", encoding="utf-8") as handle:
            handle.writelines(f"{key}={value}{os.linesep}" for key, value in result.items())
    print(f"发布门禁通过：{result['version']}，预发布={result['prerelease']}")


if __name__ == "__main__":
    main()
