"""授权私有文件名与分发过滤；构建宿主无需导入认证运行时。"""
from pathlib import Path

LICENSE_FILENAME = "license.txt"
ACTIVATION_FILENAME = "license-activation.json"


def is_private_license_file(name: str) -> bool:
    filename = Path(name).name.casefold()
    return filename in {LICENSE_FILENAME, ACTIVATION_FILENAME} or filename.startswith(".license-")
