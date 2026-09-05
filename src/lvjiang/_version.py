"""版本号 - 手动维护，打包时由 package.bat 二次覆写

开发期：进入新版本开发时，手动改成待发布版本号，与 pyproject.toml 保持一致。
打包时：`packaging/inject_version.py` 以 pyproject.toml 为准再写一次，
两边不一致会打印警告并以 pyproject.toml 为准。
"""

__version__ = "0.10.3"
