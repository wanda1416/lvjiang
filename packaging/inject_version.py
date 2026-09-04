"""版本注入脚本 - 以 pyproject.toml 为准二次覆写 _version.py

`_version.py` 平时由开发者手动维护（进入新版本开发时改成待发布版本号），
这样开发环境和源码安装都能读到真实版本；打包时再以 pyproject.toml 覆写一次，
避免手改遗漏。两者不一致时打印警告，并以 pyproject.toml 为准。
"""
import re
from pathlib import Path

TEMPLATE = '''"""版本号 - 手动维护，打包时由 package.bat 二次覆写

开发期：进入新版本开发时，手动改成待发布版本号，与 pyproject.toml 保持一致。
打包时：`packaging/inject_version.py` 以 pyproject.toml 为准再写一次，
两边不一致会打印警告并以 pyproject.toml 为准。
"""

__version__ = "{version}"
'''


def _read_version(text: str) -> str | None:
    match = re.search(r'^__version__\s*=\s*"([^"]+)"', text, re.MULTILINE)
    return match.group(1) if match else None


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

    # 覆写 _version.py，顺带提示手改遗漏
    version_file = Path("src/lvjiang/_version.py")
    if version_file.exists():
        current = _read_version(version_file.read_text(encoding="utf-8"))
        if current and current != version:
            print(
                f"警告: _version.py 为 {current}，与 pyproject.toml 的 {version} 不一致，"
                f"按 pyproject.toml 覆写"
            )

    version_file.write_text(TEMPLATE.format(version=version), encoding="utf-8")
    print(f"已写入: {version_file}")
    return 0


if __name__ == "__main__":
    exit(main())
