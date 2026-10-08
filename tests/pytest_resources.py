"""修复 pytest 编号临时目录扫描的资源释放，不过滤任何警告。

pytest 9.1.1 的 _pytest.pathlib.find_prefixed 直接 yield os.scandir 的结果，
消费者提前终止时可能留下未关闭的迭代器。先在上下文内读完目录，再产出匹配项，
确保消费方关闭、抛异常或放弃生成器时都没有存活的目录句柄。

只替换测试进程中的这个私有函数；保留 DirEntry、目录顺序及不区分大小写的前缀语义。
上游修复该资源释放路径后可移除此补丁，不修改环境安装包或生产代码。
"""

from collections.abc import Iterator
from os import DirEntry, scandir
from pathlib import Path

import _pytest.pathlib


def closed_find_prefixed(root: Path, prefix: str) -> Iterator[DirEntry[str]]:
    prefix = prefix.lower()
    with scandir(root) as entries:
        matches = [entry for entry in entries if entry.name.lower().startswith(prefix)]
    yield from matches


def install_closed_directory_scan() -> None:
    _pytest.pathlib.find_prefixed = closed_find_prefixed
