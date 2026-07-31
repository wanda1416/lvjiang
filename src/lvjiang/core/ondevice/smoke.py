"""设备端 OCR 链路分步自检 — 由 adb 触发，结果打到 logcat

用途是把「OCR 在手机上到底跑不跑得起来」拆成可定位的若干步，任一步失败都能直接
看出断在哪一环，而不是只拿到一个笼统的异常。由 MainActivity 响应
`am start ... --es selftest ocr` 调用，见 run()。

刻意不依赖外部图片：用 cv2.putText 现场合成一张，因此不需要先往设备里推文件，
自检可以在任何时候单独跑。代价是合成图只有 ASCII（Hershey 字体没有中文字形），
所以 target="ocr" 只回答「det/cls/rec 三段链路是否贯通」。

target="e2e" 则跑完整的三通道闭环：真实截屏 → 整屏 OCR（含中文）→ 点击 OCR 定位到
的按钮 → 再截一张确认点击真的生效，需要 Shizuku 已授权。
"""

import time
import traceback
from pathlib import Path

_LINE = "-" * 60
_TAG = "LvjiangSelfTest"

#: 报告结束哨兵，adb 侧靠它判断已经输出完整，不必等固定秒数
END = "=== SELFTEST END ==="


def _log_path() -> Path:
    """报告落盘位置（应用私有目录，adb 侧用 run-as 读回）"""
    import os

    home = os.environ.get("HOME") or "/data/local/tmp"
    return Path(home) / "selftest.log"


def _log(line: str) -> None:
    """即刻输出一行：logcat + 落盘各写一份

    实测这台设备会把普通应用的 Log 从 logcat 里滤掉（整个进程一行都不出），
    所以文件才是可靠通道，logcat 只作为顺手可看的附加。两边都不能因为写失败
    而把自检本身带崩，各自吞掉异常。
    """
    text = str(line)
    try:
        from android.util import Log

        for one in text.splitlines() or [""]:
            Log.i(_TAG, one)
    except Exception:
        print(text)

    try:
        with _log_path().open("a", encoding="utf-8") as fp:
            fp.write(text + "\n")
    except Exception:
        pass


def _reset_log() -> None:
    """清空上一轮报告，避免 adb 侧读到旧内容里的哨兵行就以为本轮已完成"""
    try:
        path = _log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")
    except Exception:
        pass


class _Report(list):
    """append 即输出的报告缓冲

    整份报告要等 run() 返回后才交给 Kotlin，一旦中途卡住（Binder 阻塞、
    Shizuku 没激活等）就什么都看不到，只能靠猜。每行落地时同步输出一条，
    卡在哪一步一目了然。
    """

    def append(self, line):
        super().append(line)
        _log(line)


def _step(report: list[str], title: str, fn):
    """跑一步并记录结果，返回 (是否成功, 返回值)

    失败不抛出：后续步骤大多依赖前面的产物，但即便如此也要把完整报告打完，
    否则一次 adb 往返只能定位一个问题。
    """
    report.append(f"\n{_LINE}\n[{title}]")
    started = time.time()
    try:
        value = fn()
    except Exception:
        report.append(f"FAILED ({time.time() - started:.2f}s)")
        report.append(traceback.format_exc().rstrip())
        return False, None
    report.append(f"OK ({time.time() - started:.2f}s)")
    return True, value


def _env_info(report: list[str]):
    import os
    import sys

    import cv2
    import numpy as np
    import PIL
    import yaml

    report.append(f"python     {sys.version.split()[0]}")
    report.append(f"numpy      {np.__version__}")
    report.append(f"cv2        {cv2.__version__}")
    report.append(f"Pillow     {PIL.__version__}")
    report.append(f"PyYAML     {yaml.__version__}")
    report.append(f"HOME       {os.environ.get('HOME')}")
    # 确认 srcDir 那条链路真的生效了，而不是从别处捡到的同名包
    import lvjiang

    report.append(f"lvjiang    {lvjiang.__file__}")


def _models(report: list[str]):
    """确认三个 .onnx 已被解压成真实文件

    OnnxBridge 走 java.io.File 打开模型，拿不到「APK 内的虚拟路径」。Chaquopy 对
    data file 的规则是「导入所在包时解压到文件系统」，所以这一步必须在 import
    rapidocr_onnxruntime 之后验证，且要逐个确认 is_file() 与字节数。
    """
    import rapidocr_onnxruntime

    root = Path(rapidocr_onnxruntime.__file__).parent
    report.append(f"package    {root}")

    models = sorted((root / "models").glob("*.onnx"))
    if not models:
        raise RuntimeError(f"{root / 'models'} 下没有 .onnx，data file 未被解压")
    for path in models:
        report.append(f"{path.stat().st_size / 1048576:8.2f} MB  {path.name}")
    return models


def _patch(report: list[str]):
    from lvjiang.core.ondevice.onnx_session import install

    install()
    # 替换是否真的落在 rapidocr 会用到的那些名字上，就地核一遍
    from rapidocr_onnxruntime.ch_ppocr_det.utils import DBPostProcess
    from rapidocr_onnxruntime.ch_ppocr_rec import text_recognize

    report.append(f"unclip         -> {DBPostProcess.unclip.__name__}")
    report.append(f"OrtInferSession-> {text_recognize.OrtInferSession.__name__}")


def _build(report: list[str]):
    from rapidocr_onnxruntime import RapidOCR

    engine = RapidOCR()
    report.append("RapidOCR 构造完成（三个模型的 OnnxBridge 会话均已建立）")
    return engine


def _synth_image():
    """合成一张高对比度文字图

    白底黑字、字号够大，目的是让 det 一定能框到、rec 一定有东西可认；
    真实截图的复杂度留给 P1-f。
    """
    import cv2
    import numpy as np

    img = np.full((220, 640, 3), 255, dtype=np.uint8)
    cv2.putText(img, "LVJIANG 2026", (30, 90),
                cv2.FONT_HERSHEY_SIMPLEX, 1.8, (0, 0, 0), 4)
    cv2.putText(img, "OCR ON DEVICE", (30, 180),
                cv2.FONT_HERSHEY_SIMPLEX, 1.5, (0, 0, 0), 3)
    return img


def _fingerprint(img) -> str:
    """图像指纹：用于判定 PC 与设备的合成图是否逐像素相同

    合成图是各自用本地 cv2 画的（设备 4.5.1 / PC 5.0.0）。若两端识别结果有差异，
    必须先排除「putText 渲染本身跟版本有关」这个可能，否则会把输入差异误归为
    推理引擎差异。除 md5 外也报非白像素数：md5 不同时它能看出差了多少。
    """
    import hashlib

    import numpy as np

    nonwhite = int((img < 255).any(axis=2).sum())
    return f"md5={hashlib.md5(np.ascontiguousarray(img)).hexdigest()} 非白像素={nonwhite}"


def _recognize(report: list[str], engine):
    import cv2

    img = _synth_image()
    report.append(f"输入图 {img.shape}  cv2={cv2.__version__}")
    report.append(f"指纹 {_fingerprint(img)}")

    started = time.time()
    result, elapsed = engine(img)
    report.append(f"推理耗时 {time.time() - started:.2f}s  内部计时 {elapsed}")

    if not result:
        raise RuntimeError("识别结果为空：det 没框到任何文字")

    report.append(f"识别到 {len(result)} 个文本框：")
    for _box, text, score in result:
        report.append(f"  {score:.3f}  {text!r}")
    return [text for _, text, _ in result]


# ─── 配置加载链路验证（Phase 2）────────────────────────────────────
#
# 验证 App.kt 解压的 assets 能被 Python 侧正确找到并解析。
# 不需要无障碍/Shizuku，纯文件系统 + YAML 解析。

def _run_config() -> str:
    report = _Report(["=== 设备端配置加载自检 target=config ==="])
    _log(report[0])

    ok, _ = _step(report, "1/4 运行时与依赖版本", lambda: _env_info(report))

    if ok:
        def check_constants(report):
            from ...constants import PROJECT_ROOT, SCENES_CONFIG_PATH, SYSTEM_CONFIG_DIR
            report.append(f"PROJECT_ROOT       {PROJECT_ROOT}")
            report.append(f"SYSTEM_CONFIG_DIR  {SYSTEM_CONFIG_DIR}")
            report.append(f"SCENES_CONFIG_PATH {SCENES_CONFIG_PATH}")
            if not SYSTEM_CONFIG_DIR.is_dir():
                raise RuntimeError(f"SYSTEM_CONFIG_DIR 不存在: {SYSTEM_CONFIG_DIR}")
            children = sorted(p.name for p in SYSTEM_CONFIG_DIR.iterdir())
            report.append(f"内容             {children}")
        ok, _ = _step(report, "2/4 constants 路径解析", lambda: check_constants(report))

    if ok:
        def check_scenes(report):
            from ...config import load_yaml
            from ...constants import SCENES_CONFIG_PATH, SYSTEM_CONFIG_DIR
            data = load_yaml(SCENES_CONFIG_PATH)
            if not data:
                raise RuntimeError(f"scenes.yaml 为空或解析失败: {SCENES_CONFIG_PATH}")
            keys = list(data.keys())
            report.append(f"scenes.yaml 顶层 key  {keys}")
            # 检查场景定义子目录
            scenes_dir = SYSTEM_CONFIG_DIR / "scenes"
            if not scenes_dir.is_dir():
                raise RuntimeError(f"scenes/ 目录不存在: {scenes_dir}")
            scene_files = sorted(p.name for p in scenes_dir.glob("*.yaml"))
            report.append(f"scenes/ 文件数      {len(scene_files)}")
            report.append(f"scenes/ 前 5 个     {scene_files[:5]}")
        ok, _ = _step(report, "3/4 scenes.yaml 解析", lambda: check_scenes(report))

    if ok:
        def check_workflows(report):
            from ...config import load_yaml
            from ...constants import WORKFLOWS_CONFIG_PATH
            from ..config_resolver import get_resolver
            data = load_yaml(WORKFLOWS_CONFIG_PATH)
            report.append(f"workflows.yaml 顶层 key  {list(data.keys())}")
            wf_files = get_resolver().enumerate_entities("workflows", "*.wf")
            report.append(f"workflows/*.wf 数         {len(wf_files)}")
            report.append(f"workflows/ 前 5 个        {wf_files[:5]}")
        ok, _ = _step(report, "4/4 workflows 解析", lambda: check_workflows(report))

    report.append(f"\n{_LINE}\n结论：{'配置加载全部通过' if ok else '存在失败步骤（见上方 FAILED）'}")
    return "\n".join(report)


def _run_workflow() -> str:
    """工作流引擎自检：验证模块导入 + 布局加载 + .wf 解析"""
    report = _Report(["=== 设备端工作流引擎自检 target=workflow ==="])
    _log(report[0])

    ok, _ = _step(report, "1/4 运行时与依赖版本", lambda: _env_info(report))

    if ok:
        def check_loguru(report):
            import loguru
            report.append(f"loguru  {loguru.__version__}")
        ok, _ = _step(report, "2/4 loguru 可用", lambda: check_loguru(report))

    if ok:
        def check_layout(report):
            import json

            from ...core.scene_registry import Layout
            from ..config_resolver import get_resolver
            from .workflow_runner import _default_layout_name
            name = _default_layout_name()
            layout_path = get_resolver().resolve_read(f"layouts/{name}.json")
            if layout_path is None:
                raise RuntimeError(f"布局文件不存在: layouts/{name}.json")
            data = json.loads(layout_path.read_text(encoding="utf-8"))
            layout = Layout.from_dict(name, data)
            scene_count = len(set(layout.scenes) | set(layout.points) | set(layout.arrows) | set(layout.panels))
            report.append(f"布局文件    {layout_path.name}")
            report.append(f"场景数      {scene_count}")
            report.append(f"canvas      {layout.canvas}")
            return layout
        ok, layout = _step(report, "3/4 布局加载", lambda: check_layout(report))

    if ok:
        def check_wf_parse(report):
            from ...workflows.grammar import parse_file
            from ..config_resolver import get_resolver
            resolver = get_resolver()
            wf_names = resolver.enumerate_entities("workflows", "*.wf")
            if not wf_names:
                raise RuntimeError("workflows/ 下没有 .wf 文件")
            # 只解析第一个，验证语法解析器可用
            wf = resolver.resolve_read(f"workflows/{wf_names[0]}")
            program = parse_file(wf)
            report.append(f"解析        {wf.name}")
            report.append(f"顶层语句数  {len(program.body)}")
            report.append(f"过程定义数  {len(program.procs)}")
            report.append(f"import 数   {len(program.imports)}")
        ok, _ = _step(report, "4/4 .wf 文件解析", lambda: check_wf_parse(report))

    report.append(f"\n{_LINE}\n结论：{'工作流引擎自检通过' if ok else '存在失败步骤（见上方 FAILED）'}")
    return "\n".join(report)


def _run_execute() -> str:
    """实际执行一个测试工作流，验证引擎执行链路"""
    report = _Report(["=== 设备端工作流执行自检 target=run ==="])
    _log(report[0])

    ok, _ = _step(report, "1/3 运行时与依赖版本", lambda: _env_info(report))

    if ok:
        def create_and_run(report):
            from ..config_resolver import get_resolver
            from .workflow_runner import create_engine

            # 创建工作流引擎
            engine = create_engine()
            report.append("引擎创建成功")

            # 执行测试工作流
            wf_path = get_resolver().resolve_read("workflows/device_smoke_test.wf")
            if wf_path is None:
                raise RuntimeError("测试工作流不存在: workflows/device_smoke_test.wf")

            report.append(f"开始执行: {wf_path.name}")
            result = engine.execute(wf_path)
            report.append(f"执行完成，结果: {result}")
            report.append(f"变量表: {engine.variables}")
            return engine

        ok, engine = _step(report, "2/3 执行测试工作流", lambda: create_and_run(report))

    if ok:
        def verify_result(report):
            # 验证循环计数正确（DSL 的 eval 算术返回 float）
            count = engine.variables.get("count")
            if count != 5 and count != 5.0:
                raise RuntimeError(f"count 应为 5，实际为 {count}")
            report.append(f"count = {count} [OK]")

            msg = engine.variables.get("msg")
            if msg != "hello from device":
                raise RuntimeError(f"msg 应为 'hello from device'，实际为 {msg}")
            report.append(f"msg = {msg!r} [OK]")

        ok, _ = _step(report, "3/3 验证执行结果", lambda: verify_result(report))

    report.append(f"\n{_LINE}\n结论：{'工作流执行通过' if ok else '存在失败步骤（见上方 FAILED）'}")
    return "\n".join(report)


#: task 自检用的任务 id（config/system/workflows/device_smoke_test.wf，纯计算不点游戏）
_TASK_ID = "device_smoke_test"

#: 轮询上限。首次跑要含 OCR 模型加载（秒级），给足余量
_TASK_TIMEOUT = 90.0


def _run_task() -> str:
    """走悬浮图标同一条路径的自检

    target=run 验的是「引擎能不能执行 .wf」，这里验的是「悬浮图标点下去后
    那整条链能不能跑」：task_runner 的发现 / 启动 / 轮询 / 终态 / 互斥。
    Kotlin 侧只是把这几个函数的 JSON 结果画成按钮，所以这一步全绿就意味着
    任务入口可用，不需要靠手点验证。
    """
    import json

    from . import task_runner

    report = _Report(["=== 设备端任务入口自检 target=task ==="])
    _log(report[0])

    ok, _ = _step(report, "1/5 运行时与依赖版本", lambda: _env_info(report))

    if ok:
        def check_list(report):
            data = json.loads(task_runner.list_tasks())
            if not data["ok"]:
                raise RuntimeError(f"任务清单获取失败: {data['error']}")
            ids = [t["id"] for t in data["tasks"]]
            report.append(f"任务数      {len(ids)}")
            for t in data["tasks"]:
                report.append(f"  {t['source']:5s} {t['id']:24s} {t['name']}")
            if _TASK_ID not in ids:
                raise RuntimeError(f"清单里没有 {_TASK_ID}，已有：{ids}")

        ok, _ = _step(report, "2/5 任务清单", lambda: check_list(report))

    if ok:
        def check_start(report):
            data = json.loads(task_runner.start_task(_TASK_ID))
            report.append(f"start_task -> {data}")
            if not data["ok"]:
                raise RuntimeError(data["message"])

        ok, _ = _step(report, f"3/5 启动任务 {_TASK_ID}", lambda: check_start(report))

    final = None
    if ok:
        def poll(report):
            # 这里故意不直接 join 任务线程：Kotlin 侧看到的就是轮询出来的状态，
            # 自检也走同一条路径，才能暴露「状态永远不收敛」这类问题。
            deadline = time.time() + _TASK_TIMEOUT
            seen_running = False
            while time.time() < deadline:
                status = json.loads(task_runner.get_status())
                state = status["state"]
                if state == "running":
                    seen_running = True
                elif state != "idle":
                    report.append(f"终态        {state}（耗时 {status['elapsed']}s）")
                    report.append(f"消息        {status['message']}")
                    for line in status["logs"][-6:]:
                        report.append(f"  {line}")
                    if not seen_running:
                        report.append("注：未捕获到 running 中间态（任务太快）")
                    return status
                time.sleep(0.3)
            raise RuntimeError(f"{_TASK_TIMEOUT}s 内未收敛到终态")

        ok, final = _step(report, "4/5 轮询至终态", lambda: poll(report))

    if ok:
        def verify(report):
            if final["state"] != "done":
                raise RuntimeError(f"终态应为 done，实际为 {final['state']}：{final['message']}")
            report.append("终态 done [OK]")

            # 空闲时请求停止应该被拒，否则 Kotlin 侧会把已经结束的任务报成「已停止」
            stopped = json.loads(task_runner.stop_task())
            if stopped["ok"]:
                raise RuntimeError(f"空闲时 stop_task 应返回 ok=false，实际 {stopped}")
            report.append(f"空闲时 stop_task 被拒 [OK]：{stopped['message']}")

            if task_runner.is_running():
                raise RuntimeError("is_running 应为 False")
            report.append("is_running = False [OK]")

        ok, _ = _step(report, "5/5 验证终态与互斥", lambda: verify(report))

    report.append(f"\n{_LINE}\n结论：{'任务入口链路通过' if ok else '存在失败步骤（见上方 FAILED）'}")
    return "\n".join(report)


def run(target: str = "ocr") -> str:
    """自检入口，返回完整报告文本（同时已逐行落盘 + 打进 logcat）

    无论走哪条分支、有没有炸，finally 都要补上哨兵行：adb 侧就是靠它区分
    「跑完了」和「还在跑 / 已经卡死」。

    Args:
        target: "ocr" 只验 OCR 链路（合成图，不需要 Shizuku）；
            "e2e" 验完整三通道闭环（截图 → OCR → 点击 → 再截图确认生效）；
            "config" 验系统配置加载链路（PROJECT_ROOT / scenes.yaml / workflows）；
            "workflow" 验工作流引擎（loguru / 布局加载 / .wf 解析）；
            "run" 实际执行测试工作流（变量赋值 + 循环 + 日志）；
            "task" 验悬浮图标的任务入口链路（清单 / 启动 / 轮询 / 终态）。
    """
    _reset_log()
    try:
        if target == "e2e":
            return _run_e2e()
        if target == "config":
            return _run_config()
        if target == "workflow":
            return _run_workflow()
        if target == "run":
            return _run_execute()
        if target == "task":
            return _run_task()
        return _run_ocr(target)
    except Exception:
        text = "自检自身异常：\n" + traceback.format_exc().rstrip()
        _log(text)
        return text
    finally:
        _log(END)


def _run_ocr(target: str) -> str:
    report = _Report([f"=== 设备端自检 target={target} ==="])
    _log(report[0])

    ok, _ = _step(report, "1/5 运行时与依赖版本", lambda: _env_info(report))
    if ok:
        ok, _ = _step(report, "2/5 OCR 模型落地检查", lambda: _models(report))
    if ok:
        ok, _ = _step(report, "3/5 打补丁（unclip + OrtInferSession）", lambda: _patch(report))
    engine = None
    if ok:
        ok, engine = _step(report, "4/5 构造 RapidOCR", lambda: _build(report))
    if ok and engine is not None:
        ok, _ = _step(report, "5/5 合成图识别", lambda: _recognize(report, engine))

    report.append(f"\n{_LINE}\n结论：{'全部通过' if ok else '存在失败步骤（见上方 FAILED）'}")
    return "\n".join(report)


# ─── 三通道闭环（P1-f）────────────────────────────────────────────
#
# 被点的目标就是本 App 自己的「自检：点击回显」按钮：它一被点到，状态行就会变成
# 「点击已生效」，而「生效」这两个字在点击前的界面上一处不存在。于是「点击确实生效」
# 这件事可以由下一次截图 + OCR 自己读出来，不需要人眼确认，也不需要额外造一个被测对象。

_TARGET_HINTS = ("回显", "点击回")
_EFFECT_HINTS = ("生效",)


def _a11y_channel(report: list[str]):
    """无障碍通道自检

    服务实例由系统在开关打开时创建，拿不到就只有一个原因：开关没开（或被系统关掉）。
    顺便把 capabilities 打出来，用于确认配置 xml 里的 canTakeScreenshot /
    canPerformGestures 真的生效了（Android 14+ 不声明就截不了图）。
    """
    from . import a11y

    if not a11y.is_ready():
        raise RuntimeError(
            "无障碍服务未连接：需先开启开关"
            "（adb shell settings put secure enabled_accessibility_services ...）"
        )
    report.append(f"capabilities   {a11y.capabilities()}")


def _shell_channel(report: list[str]):
    """Shizuku shell 通道自检（可选通道，不影响闭环结论）"""
    from . import shell

    # 先报 Shizuku 本身的存活与授权，再去 bind：三个状态的失败原因完全不同
    # （未激活 / 未授权 / 服务绑定失败），合成一条「通道不可用」无法定位。
    report.append(f"shizuku alive  {shell.is_shizuku_alive()}")
    report.append(f"permission     {shell.has_permission()}")
    if not shell.is_ready():
        raise RuntimeError("shell 通道未就绪：Shizuku 未激活或未授权")
    report.append(f"id -> {shell.exec_text('id')}")


def _grab(report: list[str], cap, tag: str):
    report.append(f"{tag} … 发截图请求")
    img = cap.capture()
    if img is None:
        raise RuntimeError("截图失败（详见上方 [A11yCapture] 行）")
    h, w = img.shape[:2]
    report.append(f"{tag} {w}x{h}")
    return img


def _ocr_texts(report: list[str], engine, img, tag: str):
    """识别整屏，返回 [(文本, 中心坐标)]

    截图坐标即设备坐标（整屏截图，与 dispatchGesture 同一坐标系），所以框中心
    可以直接拿去点，中间不需要任何映射。
    """
    started = time.time()
    result, _ = engine(img)
    report.append(f"{tag} 耗时 {time.time() - started:.2f}s，{len(result or [])} 个文本框")
    if not result:
        return []

    items = []
    for box, text, score in result:
        cx = int(sum(p[0] for p in box) / 4)
        cy = int(sum(p[1] for p in box) / 4)
        items.append((text, (cx, cy)))
        report.append(f"  {score:.3f}  ({cx:4d},{cy:4d})  {text!r}")
    return items


def _pick_target(items):
    for text, center in items:
        low = text.lower()
        if any(h in low for h in _TARGET_HINTS):
            return text, center
    raise RuntimeError(f"OCR 结果里找不到目标按钮（关键词 {_TARGET_HINTS}）")


def _run_e2e() -> str:
    report = _Report(["=== 设备端三通道闭环自检 target=e2e ==="])
    _log(report[0])

    ok, _ = _step(report, "1/7 运行时与依赖版本", lambda: _env_info(report))
    if ok:
        ok, _ = _step(report, "2/7 无障碍通道", lambda: _a11y_channel(report))
    if not ok:
        report.append(f"\n{_LINE}\n结论：失败（见上方 FAILED）")
        return "\n".join(report)

    from .capture import A11yCapture
    from .input import A11yInput

    cap = A11yCapture()
    ok, img = _step(report, "3/7 截图通道", lambda: _grab(report, cap, "截到"))

    engine = None
    if ok:
        ok, _ = _step(report, "4/7 打补丁", lambda: _patch(report))
    if ok:
        ok, engine = _step(report, "5/7 构造 RapidOCR", lambda: _build(report))

    target = None
    if ok and engine is not None:
        ok, target = _step(
            report, "6/7 整屏 OCR + 定位目标按钮",
            lambda: _pick_target(_ocr_texts(report, engine, img, "OCR#1")),
        )

    if ok and target is not None:
        text, center = target
        report.append(f"命中 {text!r} @ {center}")

        def click_and_verify():
            A11yInput().click_screen(center[0], center[1], poi_name="点击回显按钮")
            # 两个理由必须等：界面文本由 UI 线程异步更新；takeScreenshot 本身有
            # 数百毫秒级节流，紧接着再截会直接报 INTERVAL_TIME_SHORT。
            time.sleep(1.5)
            after = _grab(report, cap, "点击后截到")
            texts = [t for t, _ in _ocr_texts(report, engine, after, "OCR#2")]
            joined = " ".join(texts).lower()
            hit = [h for h in _EFFECT_HINTS if h in joined]
            if not hit:
                raise RuntimeError(f"点击后界面未出现预期文本（关键词 {_EFFECT_HINTS}）")
            report.append(f"点击生效：界面出现 {hit}")

        ok, _ = _step(report, "7/7 点击目标 + 再截图确认生效", click_and_verify)

    # Shizuku 降级为可选通道，单独报一步作为参考，失败不影响闭环结论
    _step(report, "附：shell 通道（可选，Shizuku）", lambda: _shell_channel(report))

    report.append(f"\n{_LINE}\n结论：{'三通道闭环全部通过' if ok else '存在失败步骤（见上方 FAILED）'}")
    return "\n".join(report)
