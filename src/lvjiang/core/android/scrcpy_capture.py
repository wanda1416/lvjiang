"""AndroidStreamCapture — 基于 scrcpy 4.1 server 协议的推送式截图后端

与 AdbCapture（拉取式，每次 capture() 触发 adb screencap）不同，
本后端采用推送式模型：

- 在设备上启动 scrcpy 4.1 server（app_process 加载 scrcpy-server.jar）
- 通过 adb forward + TCP socket 连接 server
- 后台线程持续读取 H.264 视频流 → PyAV 增量解码 → 原子更新 _latest_frame
- capture() 直接返回最新帧副本（~0ms，无 IO）
- 提供 on_frame 回调供 UI 订阅，实现预览区实时视频流

scrcpy 4.1 协议要点（已通过源码 + 实测验证）：
- server 参数：4.1 scid=<8位hex> log_level=info audio=false max_size=N max_fps=N
               control=false tunnel_forward=true cleanup=false
- socket 名：scrcpy_<scid:08x>（localabstract）
- tunnel_forward=true：server 用 LocalServerSocket 监听，client 通过 adb forward 连接
- forward 连接时 server 先发 1 字节 dummy byte（0x00）用于检测连接错误
- 协议头：1 字节 dummy → 64 字节设备名（UTF-8, 零填充）→ 4 字节 codec ID → 12 字节 session packet
- 后续 packet：方向/尺寸变化时可再次发送无 payload 的 session packet；
  media packet 为 12 字节帧头（flags + PTS + size）+ H.264 payload
"""

import random
import socket
import struct
import subprocess
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
from loguru import logger

from ...constants import PROJECT_ROOT
from ..capture_base import CaptureBackend
from ..platforms import SUBPROCESS_NO_WINDOW
from .device import AdbDevice

if TYPE_CHECKING:
    import av

# scrcpy server jar 本地路径（相对于项目 data 目录）
_JAR_RELATIVE = Path("data") / "scrcpy" / "scrcpy-server.jar"
# 设备端 jar 路径
_REMOTE_JAR_PATH = "/data/local/tmp/scrcpy-server.jar"
# 默认视频端口
_VIDEO_PORT = 27183
# scrcpy 4.1 协议：forward 连接先发 1 字节 dummy byte
_DUMMY_BYTE_SIZE = 1
# scrcpy 4.1 协议：设备名固定 64 字节
_DEVICE_META_SIZE = 64
# server 版本（必须与 jar 匹配）
_SERVER_VERSION = "4.1"


class AndroidStreamCapture(CaptureBackend):
    """基于 scrcpy 4.1 server 协议的截图后端

    后台线程持续解码 H.264 帧，capture() 零延迟返回最新帧。
    通过 set_on_frame() 订阅帧回调，可驱动 UI 实时预览。
    """

    def __init__(
        self,
        device: AdbDevice,
        max_size: int = 0,
        max_fps: int = 15,
        jar_path: str | Path | None = None,
    ):
        """
        Args:
            device: AdbDevice 实例
            max_size: 输出画面较长边的上限像素，0 表示不限制（使用设备原始分辨率，默认）
                      注意：工作流坐标与 input tap 共享同一坐标系，截图必须保持原始分辨率
            max_fps: 帧率上限，默认 15
            jar_path: scrcpy-server.jar 路径；None 则使用项目 data/scrcpy/ 下默认路径
        """
        self._device = device
        self._max_size = max_size
        self._max_fps = max_fps

        if jar_path is not None:
            self._jar_local = Path(jar_path)
        else:
            # 相对于项目根目录；根的定位统一由 constants.PROJECT_ROOT 负责，
            # 免得这里自己数 __file__ 的层级（目录一挪就错）
            self._jar_local = PROJECT_ROOT / _JAR_RELATIVE

        self._scid: int = 0
        self._socket_name: str = ""
        self._server_proc: subprocess.Popen | None = None
        self._sock: socket.socket | None = None
        self._decoder: "av.CodecContext | None" = None
        self._latest_frame: np.ndarray | None = None
        self._frame_lock = threading.Lock()
        self._decoder_lock = threading.Lock()  # 保护 decoder.parse/decode 的线程安全
        self._on_frame_callback = None
        self._running = False
        self._decode_thread: threading.Thread | None = None
        self._size: tuple[int, int] | None = None
        self._session_size: tuple[int, int] | None = None
        self._ready_event = threading.Event()
        self._transitioning = True
        self._generation = 0
        self._frame_sequence = 0
        self._pending_session = True
        self._codec_config = b""

    @property
    def max_fps(self) -> int:
        """帧率上限（供录屏等外部消费方读取）"""
        return self._max_fps

    @property
    def generation(self) -> int:
        """成功解码的画面 session 代次；方向/尺寸切换后递增。"""
        return self._generation

    @property
    def frame_sequence(self) -> int:
        """每发布一个新解码帧递增，等待方可据此排除冻结的旧帧。"""
        return self._frame_sequence

    @property
    def is_transitioning(self) -> bool:
        """scrcpy 已通知尺寸变化、但新画面尚未成功解码。"""
        return self._transitioning

    # ─── 生命周期 ─────────────────────────────────────────

    def start(self) -> bool:
        """推送 jar → 启动 server → 建立连接 → 读协议头 → 启动解码线程 → 等待首帧"""
        try:
            import av  # noqa: F401
        except ImportError:
            logger.error(
                "[AndroidStream] 未安装 PyAV，无法解码 H.264 流。请执行 `pip install av`"
            )
            return False

        try:
            self._running = True
            self._ready_event.clear()
            self._transitioning = True
            self._pending_session = True
            self._codec_config = b""
            self._scid = random.randint(1, 0x7FFFFFFF)
            self._socket_name = f"scrcpy_{self._scid:08x}"

            # 0. 确定 max_size：0 表示使用设备原始分辨率（取较长边）
            if self._max_size <= 0:
                dev_w, dev_h = self._device.get_resolution()
                self._max_size = max(dev_w, dev_h)
                logger.debug(f"[AndroidStream] max_size=0，使用设备分辨率较长边: {self._max_size}")

            # 1. 推送 jar
            if not self._push_jar():
                self._running = False
                return False

            # 2. 清理旧 forward + 建立新 forward
            self._cleanup_forward()
            r = subprocess.run(
                [*self._device._base(), "forward",
                 f"tcp:{_VIDEO_PORT}", f"localabstract:{self._socket_name}"],
                capture_output=True, text=True, timeout=10, **SUBPROCESS_NO_WINDOW,
            )
            if r.returncode != 0:
                logger.error(f"[AndroidStream] adb forward 失败: {r.stderr.strip()}")
                self._running = False
                return False
            logger.debug(f"[AndroidStream] adb forward tcp:{_VIDEO_PORT} -> localabstract:{self._socket_name}")

            # 3. 启动 server
            if not self._start_server():
                self._running = False
                return False

            # 4. 连接 socket
            if not self._connect_socket():
                self._running = False
                return False

            # 5. 读协议头（device meta + codec id + session packet）
            # server 可能还没完全就绪，给 3 次重试机会
            for attempt in range(3):
                if self._read_protocol_header():
                    break
                if attempt < 2:
                    logger.debug(f"[AndroidStream] 协议头读取失败，重试 ({attempt + 1}/3)")
                    # 关闭当前 socket，等待后重连
                    if self._sock:
                        try:
                            self._sock.close()
                        except Exception:
                            pass
                        self._sock = None
                    time.sleep(0.5)
                    if not self._connect_socket():
                        self._running = False
                        return False
            else:
                logger.error("[AndroidStream] 3 次尝试均无法读取协议头")
                self._running = False
                return False

            # 6. 启动解码线程
            with self._decoder_lock:
                self._decoder = av.CodecContext.create("h264", "r")
            self._decode_thread = threading.Thread(
                target=self._decode_loop, daemon=True, name="android-stream-decode"
            )
            self._decode_thread.start()

            # 7. 等待首帧
            deadline = time.monotonic() + 5.0
            while not self._ready_event.is_set() and time.monotonic() < deadline:
                if not self._running:
                    break
                time.sleep(0.05)

            if self._latest_frame is None:
                logger.error("[AndroidStream] 5 秒内未收到首帧")
                self.stop()
                return False

            h, w = self._latest_frame.shape[:2]
            self._size = (w, h)
            logger.info(
                f"[AndroidStream] 启动成功 scrcpy={_SERVER_VERSION} "
                f"分辨率={w}x{h} max_fps={self._max_fps} max_size={self._max_size}"
            )
            return True

        except Exception as e:  # noqa: BLE001
            logger.error(f"[AndroidStream] 启动失败: {e}")
            self.stop()
            return False

    def stop(self):
        """停止解码线程 → 关闭 socket → 终止 server → 清理 forward"""
        self._running = False
        self._ready_event.clear()
        self._transitioning = True

        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None

        if self._server_proc:
            try:
                self._server_proc.terminate()
                self._server_proc.wait(timeout=3.0)
            except Exception:
                try:
                    self._server_proc.kill()
                except Exception:
                    pass
            self._server_proc = None

        decode_thread_stuck = False
        if self._decode_thread and self._decode_thread.is_alive():
            self._decode_thread.join(timeout=2.0)
            if self._decode_thread.is_alive():
                # 解码线程未按时退出（可能阻塞在 decoder.parse/decode），
                # 此时不能清 _decoder，否则线程访问已释放对象触发 native crash
                decode_thread_stuck = True
                logger.warning("[AndroidStream] 解码线程 2s 内未退出，保留 decoder 引用避免竞态")
        self._decode_thread = None

        self._cleanup_forward()
        # 清理设备端 server 进程
        subprocess.run(
            [*self._device._base(), "shell", "pkill", "-f", "scrcpy-server"],
            capture_output=True, timeout=5, **SUBPROCESS_NO_WINDOW,
        )

        if not decode_thread_stuck:
            with self._decoder_lock:
                self._decoder = None
        logger.debug("[AndroidStream] 已停止")

    # ─── 截图接口（继承 CaptureBackend）────────────────────

    def capture(self, timeout: float = 5.0) -> np.ndarray | None:
        """零延迟取帧 — 返回后台线程已解码的最新帧副本"""
        with self._frame_lock:
            if self._latest_frame is None:
                return None
            return self._latest_frame.copy()

    def capture_lossless(self, timeout: float = 10.0) -> np.ndarray | None:
        """无损截图 — 回退到 adb screencap 获取高质量图像（用于 OCR）

        scrcpy 视频流使用 H.264 有损压缩，文字边缘可能出现压缩伪影影响 OCR。
        此方法通过 screencap 获取无损 PNG 截图，确保文字清晰。

        Args:
            timeout: 超时秒数（默认 10s，screencap 比视频流慢）

        Returns:
            BGR numpy 数组，失败返回 None
        """
        try:
            png_bytes = self._device.shell_bytes("exec-out", "screencap", "-p", timeout=timeout)
        except Exception as e:
            from loguru import logger
            logger.error(f"[AndroidStream] screencap 执行异常: {e}")
            return None

        if not png_bytes:
            from loguru import logger
            logger.error("[AndroidStream] screencap 返回空数据")
            return None

        try:
            import cv2
            arr = np.frombuffer(png_bytes, dtype=np.uint8)
            img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
            if img is None:
                from loguru import logger
                logger.error("[AndroidStream] PNG 解码失败")
                return None
            return img
        except Exception as e:
            from loguru import logger
            logger.error(f"[AndroidStream] screencap 解码失败: {e}")
            return None

    def get_capture_size(self) -> tuple[int, int]:
        """返回实际截图尺寸 (width, height)"""
        if self._size is not None:
            return self._size
        deadline = time.monotonic() + 3.0
        while self._size is None and time.monotonic() < deadline:
            time.sleep(0.1)
        return self._size or (0, 0)

    def wait_ready(
        self, timeout: float = 10.0, *, expected_orientation: str = "any",
    ) -> bool:
        """等待当前 session 的首个有效帧，并可校验画面方向。"""
        deadline = time.monotonic() + max(0.0, float(timeout))
        orientation = str(expected_orientation or "any").lower()
        while self._running and time.monotonic() < deadline:
            if self._ready_event.wait(timeout=min(0.1, max(0.0, deadline - time.monotonic()))):
                with self._frame_lock:
                    frame = self._latest_frame
                    if frame is None:
                        continue
                    h, w = frame.shape[:2]
                if orientation == "landscape" and w <= h:
                    time.sleep(0.05)
                    continue
                if orientation == "portrait" and h <= w:
                    time.sleep(0.05)
                    continue
                return True
        return False

    # ─── 帧回调（UI 订阅）────────────────────────────────

    def set_on_frame(self, callback):
        """订阅帧回调，实现预览区实时视频流

        Args:
            callback: callable(np.ndarray) — 接收 BGR 帧。
                      注意此回调在解码线程执行，UI 侧需通过 Qt 信号转发到主线程。
        """
        self._on_frame_callback = callback

    # ─── 内部：jar 推送 ──────────────────────────────────

    def _push_jar(self) -> bool:
        """推送 scrcpy-server.jar 到设备"""
        if not self._jar_local.exists():
            logger.error(f"[AndroidStream] scrcpy-server.jar 不存在: {self._jar_local}")
            return False
        r = subprocess.run(
            [*self._device._base(), "push", str(self._jar_local), _REMOTE_JAR_PATH],
            capture_output=True, text=True, timeout=30, **SUBPROCESS_NO_WINDOW,
        )
        if r.returncode != 0:
            logger.error(f"[AndroidStream] 推送 jar 失败: {r.stderr.strip()}")
            return False
        logger.debug(f"[AndroidStream] jar 已推送到 {_REMOTE_JAR_PATH}")
        return True

    # ─── 内部：server 管理 ────────────────────────────────

    def _start_server(self) -> bool:
        """启动 scrcpy 4.1 server 进程"""
        scid_hex = f"{self._scid:08x}"
        shell_cmd = (
            f"CLASSPATH={_REMOTE_JAR_PATH} "
            f"app_process / com.genymobile.scrcpy.Server {_SERVER_VERSION} "
            f"scid={scid_hex} log_level=info audio=false "
            f"max_size={self._max_size} max_fps={self._max_fps} "
            f"control=false tunnel_forward=true cleanup=false"
        )
        try:
            self._server_proc = subprocess.Popen(
                [*self._device._base(), "shell", shell_cmd],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                **SUBPROCESS_NO_WINDOW,
            )
            logger.debug(f"[AndroidStream] server 进程已启动 pid={self._server_proc.pid}")
        except Exception as e:
            logger.error(f"[AndroidStream] 启动 server 失败: {e}")
            self._server_proc = None
            return False

        # 等待 server 就绪（监听 socket）
        time.sleep(1.5)
        if self._server_proc.poll() is not None:
            _, stderr = self._server_proc.communicate()
            logger.error(f"[AndroidStream] server 启动失败 (rc={self._server_proc.returncode}): "
                         f"{stderr.decode(errors='replace')[:300]}")
            self._server_proc = None
            return False

        logger.debug("[AndroidStream] server 进程运行中")
        return True

    # ─── 内部：socket 连接 ────────────────────────────────

    def _connect_socket(self) -> bool:
        """通过 adb forward 连接 server 的 video socket"""
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            try:
                self._sock = socket.create_connection(("127.0.0.1", _VIDEO_PORT), timeout=2)
                self._sock.settimeout(5.0)
                logger.debug(f"[AndroidStream] 已连接到 video socket (port {_VIDEO_PORT})")
                return True
            except (ConnectionRefusedError, OSError):
                time.sleep(0.2)
        logger.error(f"[AndroidStream] 连接 video socket 超时 (port {_VIDEO_PORT})")
        return False

    # ─── 内部：协议头解析 ─────────────────────────────────

    def _read_protocol_header(self) -> bool:
        """读取 scrcpy 4.1 协议头：dummy byte + 64 字节设备名 + 4 字节 codec ID + 12 字节 session packet

        tunnel_forward=true 时 server 先发 1 字节 dummy byte（0x00），用于检测连接错误。
        """
        assert self._sock is not None
        try:
            # 0. Dummy byte（1 字节，forward 连接特有）
            dummy = self._recv_exact(_DUMMY_BYTE_SIZE)
            if dummy is None:
                logger.error("[AndroidStream] 读取 dummy byte 失败")
                return False
            logger.debug(f"[AndroidStream] dummy byte: 0x{dummy[0]:02x}")

            # 1. 设备名（64 字节固定长度，UTF-8 零填充）
            meta = self._recv_exact(_DEVICE_META_SIZE)
            if meta is None:
                logger.error("[AndroidStream] 读取设备名失败")
                return False
            device_name = meta.decode("utf-8").rstrip("\x00")
            logger.debug(f"[AndroidStream] 设备名: {device_name}")

            # 2. Codec ID（4 字节，如 b'h264'）
            codec_id = self._recv_exact(4)
            if codec_id is None:
                logger.error("[AndroidStream] 读取 codec ID 失败")
                return False
            codec_name = codec_id.decode("ascii", errors="replace")
            logger.debug(f"[AndroidStream] codec: {codec_name}")

            # 3. Session packet（12 字节：flags + width + height）
            session = self._recv_exact(12)
            if session is None:
                logger.error("[AndroidStream] 读取 session packet 失败")
                return False
            if not session[0] & 0x80:
                logger.error("[AndroidStream] 首包不是 session packet")
                return False
            self._handle_session_packet(session, initial=True)

            return True

        except Exception as e:
            logger.error(f"[AndroidStream] 读取协议头异常: {e}")
            return False

    def _recv_exact(self, n: int) -> bytes | None:
        """从 socket 精确读取 n 字节"""
        assert self._sock is not None
        buf = b""
        while len(buf) < n:
            chunk = self._sock.recv(n - len(buf))
            if not chunk:
                return None
            buf += chunk
        return buf

    def _handle_session_packet(self, header: bytes, *, initial: bool = False) -> None:
        """接收 scrcpy 在启动及显示方向变化时发送的无 payload session 包。"""
        width = struct.unpack(">I", header[4:8])[0]
        height = struct.unpack(">I", header[8:12])[0]
        previous = self._session_size
        self._session_size = (width, height)
        self._pending_session = True
        self._codec_config = b""
        self._transitioning = True
        self._ready_event.clear()
        with self._frame_lock:
            self._latest_frame = None
        logger.info(
            f"[AndroidStream] session {'初始化' if initial else '切换'}: "
            f"{previous or '-'} -> {width}x{height}")
        if not initial:
            # Android 方向变化会重建 MediaCodec。PyAV 的旧参考帧不可跨 encoder
            # session 复用，因此同步丢弃并重建解码上下文。
            try:
                import av
                with self._decoder_lock:
                    self._decoder = av.CodecContext.create("h264", "r")
            except Exception as exc:  # noqa: BLE001
                logger.error(f"[AndroidStream] session 切换后重建解码器失败: {exc}")

    @staticmethod
    def _pop_stream_packet(buf: bytes):
        """从缓冲区弹出一个 scrcpy packet；数据不足返回 ``None``。

        返回 ``(kind, header, payload, remaining)``。session 包固定 12 字节且
        没有 payload；media 包的 payload 长度才由 header[8:12] 声明。
        """
        if len(buf) < 12:
            return None
        header = buf[:12]
        if header[0] & 0x80:
            return "session", header, b"", buf[12:]
        payload_size = struct.unpack(">I", header[8:12])[0]
        if len(buf) < 12 + payload_size:
            return None
        return (
            "media", header, buf[12:12 + payload_size],
            buf[12 + payload_size:],
        )

    # ─── 内部：解码循环 ───────────────────────────────────

    def _decode_loop(self):
        """后台线程：持续读取 scrcpy 视频流 → 增量解码 → 更新最新帧

        scrcpy media packet 格式（12 字节帧头 + payload）：
        - byte 0: flags (MSB=media_packet, C=config, K=keyframe) + PTS 高 7 位
        - byte 1-7: PTS 低 56 位
        - byte 8-11: packet size (H.264 payload 大小，big-endian)
        - byte 12+: raw H.264 payload
        """

        frame_interval = 1.0 / self._max_fps if self._max_fps > 0 else 0.0
        last_frame_time = 0.0
        buf = b""

        while self._running:
            if self._sock is None:
                break
            try:
                data = self._sock.recv(65536)
            except socket.timeout:
                continue
            except OSError:
                # socket 已关闭（stop() 调用），正常退出
                break
            except Exception as e:
                if self._running:
                    logger.debug(f"[AndroidStream] socket 读取异常: {e}")
                break

            if not data:
                logger.debug("[AndroidStream] socket EOF")
                break

            buf += data

            # scrcpy 在方向/显示属性变化时会在同一流中再次插入无 payload
            # session packet，不能将其 byte 8-11 的 height 当成 payload size。
            while len(buf) >= 12:
                parsed = self._pop_stream_packet(buf)
                if parsed is None:
                    break
                kind, header, h264_data, buf = parsed
                if kind == "session":
                    self._handle_session_packet(header)
                    continue

                pts_flags = struct.unpack(">Q", header[:8])[0]
                if not h264_data:
                    logger.warning("[AndroidStream] 忽略空 media packet")
                    continue

                is_config = bool(pts_flags & (1 << 62))
                if is_config:
                    # scrcpy 官方客户端对 H.26x 将 codec config 前置到下一帧。
                    self._codec_config += h264_data
                    continue
                if self._codec_config:
                    h264_data = self._codec_config + h264_data
                    self._codec_config = b""

                # 解码 H.264 帧（加锁保护 decoder 并发访问）
                try:
                    with self._decoder_lock:
                        if self._decoder is None:
                            break
                        packets = self._decoder.parse(h264_data)
                except Exception:
                    continue

                for pkt in packets:
                    try:
                        with self._decoder_lock:
                            if self._decoder is None:
                                break
                            frames = self._decoder.decode(pkt)
                    except Exception:
                        continue
                    for frame in frames:
                        now = time.monotonic()
                        if frame_interval and now - last_frame_time < frame_interval:
                            continue
                        last_frame_time = now

                        bgr = self._frame_to_bgr(frame)
                        h, w = bgr.shape[:2]
                        target = self._session_size
                        if target is not None and (w, h) != target:
                            logger.debug(
                                f"[AndroidStream] 丢弃旧 session 帧 {w}x{h}，"
                                f"等待 {target[0]}x{target[1]}")
                            continue
                        with self._frame_lock:
                            self._latest_frame = bgr
                            self._frame_sequence += 1
                        self._size = (w, h)
                        if self._pending_session:
                            self._pending_session = False
                            self._generation += 1
                            self._transitioning = False
                            self._ready_event.set()
                            logger.info(
                                f"[AndroidStream] session 已就绪 generation="
                                f"{self._generation} frame={w}x{h}")

                        if self._on_frame_callback:
                            try:
                                self._on_frame_callback(bgr)
                            except Exception as e:
                                logger.debug(f"[AndroidStream] 帧回调异常: {e}")

            # buffer 过大时截断防止内存膨胀
            if len(buf) > 1024 * 1024:
                buf = buf[-65536:]

        # flush decoder 仅在正常退出时执行（非停止信号触发），避免退出时阻塞
        if self._running:
            with self._decoder_lock:
                if self._decoder is None:
                    return
                try:
                    for frame in self._decoder.decode():
                        bgr = self._frame_to_bgr(frame)
                        with self._frame_lock:
                            self._latest_frame = bgr
                except Exception:
                    pass

    @staticmethod
    def _frame_to_bgr(frame) -> np.ndarray:
        """将 PyAV VideoFrame 转换为 BGR numpy 数组

        注意：reformat / to_ndarray 期间必须保持 frame 引用，
        防止底层 AVFrame 被提前释放导致 numpy 数组指向已释放内存（use-after-free）。
        """
        if frame.format.name != "bgr24":
            frame = frame.reformat(format="bgr24")
        arr = frame.to_ndarray()
        # 显式保持 frame 生命周期至 ndarray 拷贝完成
        _ = frame
        return arr

    # ─── 内部：forward 清理 ───────────────────────────────

    def _cleanup_forward(self):
        """清理 adb forward 规则"""
        subprocess.run(
            [*self._device._base(), "forward", "--remove-all"],
            capture_output=True, timeout=5, **SUBPROCESS_NO_WINDOW,
        )
