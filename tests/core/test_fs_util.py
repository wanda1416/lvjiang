"""配置落盘必须与仓库的 eol=lf 一致，不随 Windows 文本模式漂移。"""
from lvjiang.core.fs_util import atomic_write_text


def test_atomic_write_text_keeps_lf(tmp_path):
    path = tmp_path / "app.yaml"
    atomic_write_text(path, "name: 中文\nitems:\n  - 1\n", prefix=".test-")
    assert path.read_bytes() == "name: 中文\nitems:\n  - 1\n".encode()
