"""版本注入脚本 - 从 pyproject.toml 读取版本号并写入 _version.py"""
import re
from pathlib import Path


def main():
    # 读取 pyproject.toml
    pyproject = Path("pyproject.toml")
    if not pyproject.exists():
        print("错误: pyproject.toml 不存在")
        return 1

    content = pyproject.read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"([^"]+)"', content, re.MULTILINE)
    if not match:
        print("错误: 无法从 pyproject.toml 读取版本号")
        return 1

    version = match.group(1)
    print(f"版本号: {version}")

    # 写入 _version.py
    version_file = Path("src/lvjiang/_version.py")
    version_file.write_text(
        f'"""版本号 - 打包时由 package.bat 自动注入\n'
        f"\n"
        f'开发环境：此文件内容为 "0.0.0.dev0"，实际版本从 pyproject.toml 读取\n'
        f"打包后：此文件会被更新为实际版本号\n"
        f'"""\n'
        f"\n"
        f'__version__ = "{version}"\n',
        encoding="utf-8",
    )
    print(f"已写入: {version_file}")
    return 0


if __name__ == "__main__":
    exit(main())
