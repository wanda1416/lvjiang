"""ScreenRecorder — 后台线程 H.264 录屏引擎

从 scrcpy 解码线程分叉喂帧（push 仅入队，绝不阻塞），
独立编码线程通过 PyAV(libx264) 边录边写待定文件（.part）：

- start() 打开容器开始录制
- pause()/resume() 暂停期间丢弃入帧，视频时间轴自然连续无空洞
- stop() flush 并封口，返回 RecordResult
- finalize() 去掉 .part 后缀转正；discard() 删除待定文件
"""

import queue
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from loguru import logger

# 待定文件后缀（录制中/待保存），保存时 rename 去掉
PART_SUFFIX = ".part"
# 帧队列上限：约 2 秒缓冲（15fps），满则丢帧保护解码线程
_QUEUE_MAX = 30


@dataclass
class RecordResult:
    """一次录制的统计结果"""
    path: Path          # 待定文件路径（.mp4.part）
    duration: float     # 视频时长（秒，按写入帧数/fps 计算）
    frames: int         # 实际写入帧数
    dropped: int        # 队列满丢弃帧数


class ScreenRecorder:
    """推送式录屏器：push() 入队 → 编码线程 libx264 → mp4

    状态：start() 后 recording；pause()/resume() 切换；stop() 终止。
    stop() 后实例不可复用，新录制需创建新实例。
    """

    def __init__(self, path: str | Path, width: int, height: int, fps: int = 15):
        """
        Args:
            path: 目标 mp4 路径（内部写入 path + '.part' 待定文件）
            width: 帧宽（奇数自动裁到偶数，yuv420p 要求）
            height: 帧高（同上）
            fps: 固定输出帧率，与 scrcpy max_fps 保持一致
        """
        self._final_path = Path(path)
        self._part_path = Path(str(path) + PART_SUFFIX)
        # yuv420p 要求偶数尺寸，奇数时裁掉最后一行/列
        self._width = width - (width % 2)
        self._height = height - (height % 2)
        self._fps = fps

        self._queue: queue.Queue[np.ndarray | None] = queue.Queue(maxsize=_QUEUE_MAX)
        self._paused = False
        self._running = False
        self._frames = 0
        self._dropped = 0
        self._error: str | None = None
        self._thread: threading.Thread | None = None
        self._start_time = 0.0

    # ─── 生命周期 ─────────────────────────────────────────

    def start(self) -> bool:
        """打开容器并启动编码线程，失败返回 False"""
        try:
            import av  # noqa: F401
        except ImportError:
            logger.error("[Recorder] 未安装 PyAV，无法录屏。请执行 `pip install av`")
            return False
        if self._width <= 0 or self._height <= 0:
            logger.error(f"[Recorder] 非法帧尺寸: {self._width}x{self._height}")
            return False
        try:
            self._part_path.parent.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            logger.error(f"[Recorder] 创建输出目录失败: {e}")
            return False

        self._running = True
        self._start_time = time.monotonic()
        self._thread = threading.Thread(
            target=self._encode_loop, daemon=True, name="screen-recorder-encode"
        )
        self._thread.start()
        logger.info(
            f"[Recorder] 开始录制 {self._width}x{self._height}@{self._fps}fps → {self._part_path}"
        )
        return True

    def push(self, bgr: np.ndarray):
        """入队一帧（解码线程调用）：暂停/停止态直接丢弃，队列满丢帧计数"""
        if not self._running or self._paused:
            return
        try:
            self._queue.put_nowait(bgr)
        except queue.Full:
            self._dropped += 1

    def pause(self):
        self._paused = True
        logger.debug("[Recorder] 已暂停")

    def resume(self):
        self._paused = False
        logger.debug("[Recorder] 已继续")

    @property
    def is_paused(self) -> bool:
        return self._paused

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def part_path(self) -> Path:
        return self._part_path

    def elapsed_video_seconds(self) -> float:
        """已写入视频时长（秒）——按帧数/fps 计，暂停期间不增长"""
        return self._frames / self._fps if self._fps > 0 else 0.0

    def stop(self) -> RecordResult:
        """停止录制：flush 编码器并封口容器，返回统计结果"""
        if self._running:
            self._running = False
            # 哨兵唤醒编码线程（队列可能为空阻塞在 get）
            try:
                self._queue.put_nowait(None)
            except queue.Full:
                pass
            if self._thread is not None:
                self._thread.join(timeout=10.0)
            self._thread = None
        result = RecordResult(
            path=self._part_path,
            duration=self.elapsed_video_seconds(),
            frames=self._frames,
            dropped=self._dropped,
        )
        logger.info(
            f"[Recorder] 录制结束 时长={result.duration:.1f}s "
            f"帧数={result.frames} 丢帧={result.dropped}"
        )
        if self._error:
            logger.error(f"[Recorder] 编码期间发生错误: {self._error}")
        return result

    # ─── 待定文件处置 ─────────────────────────────────────

    def finalize(self) -> Path | None:
        """保存：待定文件转正（去掉 .part 后缀），返回最终路径，失败返回 None"""
        if self._running:
            self.stop()
        if not self._part_path.exists():
            logger.error(f"[Recorder] 待定文件不存在: {self._part_path}")
            return None
        try:
            self._part_path.replace(self._final_path)
            logger.info(f"[Recorder] 录屏已保存: {self._final_path}")
            return self._final_path
        except Exception as e:
            logger.error(f"[Recorder] 转正失败: {e}")
            return None

    def discard(self):
        """放弃：删除待定文件"""
        if self._running:
            self.stop()
        try:
            if self._part_path.exists():
                self._part_path.unlink()
                logger.info(f"[Recorder] 已放弃录屏并删除: {self._part_path}")
        except Exception as e:
            logger.error(f"[Recorder] 删除待定文件失败: {e}")

    # ─── 内部：编码循环 ───────────────────────────────────

    def _encode_loop(self):
        """编码线程：从队列取帧 → libx264 编码 → 写入 .part 容器

        PTS 按帧序号递增（固定 fps），暂停期间无入帧，时间轴连续。
        """
        import av

        container = None
        try:
            # 待定文件后缀非 .mp4，需显式指定格式
            container = av.open(str(self._part_path), mode="w", format="mp4")
            stream = container.add_stream("libx264", rate=self._fps)
            stream.width = self._width
            stream.height = self._height
            stream.pix_fmt = "yuv420p"

            while True:
                try:
                    frame_bgr = self._queue.get(timeout=0.5)
                except queue.Empty:
                    if not self._running:
                        break
                    continue
                if frame_bgr is None:  # stop() 哨兵
                    break
                # 裁到偶数尺寸（与 stream 一致），切片后需连续内存才能进 PyAV
                if frame_bgr.shape[0] != self._height or frame_bgr.shape[1] != self._width:
                    frame_bgr = np.ascontiguousarray(
                        frame_bgr[: self._height, : self._width]
                    )
                frame = av.VideoFrame.from_ndarray(frame_bgr, format="bgr24")
                frame.pts = self._frames
                for packet in stream.encode(frame):
                    container.mux(packet)
                self._frames += 1

            # flush 编码器
            for packet in stream.encode():
                container.mux(packet)
        except Exception as e:  # noqa: BLE001
            self._error = str(e)
            logger.error(f"[Recorder] 编码线程异常: {e}")
        finally:
            if container is not None:
                try:
                    container.close()
                except Exception:
                    pass
            self._running = False

