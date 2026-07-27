"""ScreenRecorder 录屏引擎单元测试（PyAV 不可用时整体跳过）"""

import time

import numpy as np
import pytest

pytest.importorskip("av")

from src.core.screen_recorder import PART_SUFFIX, ScreenRecorder


def _frame(w: int = 64, h: int = 48, color: int = 128) -> np.ndarray:
    """合成纯色 BGR 帧"""
    return np.full((h, w, 3), color, dtype=np.uint8)


def _push_n(rec: ScreenRecorder, n: int, w: int = 64, h: int = 48):
    """逐帧入队（带微小间隔，避免瞬间打满队列）"""
    for i in range(n):
        rec.push(_frame(w, h, color=(i * 20) % 256))
        time.sleep(0.005)


class TestScreenRecorder:
    def test_record_and_stop_writes_part_file(self, tmp_path):
        """push 若干帧 → stop：.part 文件存在且非空，帧数/丢帧统计正确"""
        path = tmp_path / "rec.mp4"
        rec = ScreenRecorder(path, 64, 48, fps=15)
        assert rec.start()
        _push_n(rec, 10)
        result = rec.stop()

        assert result.frames == 10
        assert result.dropped == 0
        assert result.duration == pytest.approx(10 / 15)
        assert rec.part_path == tmp_path / ("rec.mp4" + PART_SUFFIX)
        assert rec.part_path.exists()
        assert rec.part_path.stat().st_size > 0
        assert not path.exists()

    def test_finalize_renames_part_to_mp4(self, tmp_path):
        """保存转正：.mp4 存在、.part 消失"""
        path = tmp_path / "rec.mp4"
        rec = ScreenRecorder(path, 64, 48, fps=15)
        assert rec.start()
        _push_n(rec, 5)
        rec.stop()

        final = rec.finalize()
        assert final == path
        assert path.exists()
        assert not rec.part_path.exists()

    def test_discard_deletes_part_file(self, tmp_path):
        """放弃：待定文件被删除"""
        path = tmp_path / "rec.mp4"
        rec = ScreenRecorder(path, 64, 48, fps=15)
        assert rec.start()
        _push_n(rec, 5)
        rec.stop()
        assert rec.part_path.exists()

        rec.discard()
        assert not rec.part_path.exists()
        assert not path.exists()

    def test_pause_drops_frames(self, tmp_path):
        """暂停期间 push 的帧不计入 frames，继续后恢复计入"""
        path = tmp_path / "rec.mp4"
        rec = ScreenRecorder(path, 64, 48, fps=15)
        assert rec.start()
        _push_n(rec, 4)

        rec.pause()
        assert rec.is_paused
        _push_n(rec, 6)  # 暂停态直接丢弃，不入队不计数

        rec.resume()
        assert not rec.is_paused
        _push_n(rec, 3)

        result = rec.stop()
        assert result.frames == 7  # 4 + 3
        assert result.dropped == 0

    def test_odd_size_cropped_to_even(self, tmp_path):
        """奇数尺寸裁到偶数（yuv420p 要求），奇数帧可正常编码"""
        path = tmp_path / "rec.mp4"
        rec = ScreenRecorder(path, 65, 49, fps=15)
        assert rec.start()
        for _ in range(5):
            rec.push(_frame(65, 49))
            time.sleep(0.005)
        result = rec.stop()

        assert result.frames == 5
        assert rec.part_path.exists()
        assert rec.part_path.stat().st_size > 0

    def test_stop_is_idempotent_via_finalize(self, tmp_path):
        """录制中直接 finalize：内部先 stop 再转正"""
        path = tmp_path / "rec.mp4"
        rec = ScreenRecorder(path, 64, 48, fps=15)
        assert rec.start()
        _push_n(rec, 3)

        final = rec.finalize()
        assert final == path
        assert path.exists()
        assert not rec.is_running
