"""onnxruntime 占位包：仅用于满足 rapidocr_onnxruntime 的依赖声明。

真包是 C++ 推理引擎的 Python 绑定，Chaquopy 包仓库没有它的 Android wheel。
设备端的推理由 Kotlin 侧 OnnxBridge（com.microsoft.onnxruntime:onnxruntime-android）
承担，Python 侧通过 src/lvjiang/core/ondevice/onnx_session.py 注入替代的
InferSession 实现，因此这里只需让 utils/infer_engine.py 的顶层 import 通过。

版本号取 99.0.0 以满足上游的 onnxruntime>=1.7.0 约束。
"""

from setuptools import setup

setup(name="onnxruntime", version="99.0.0", py_modules=["onnxruntime"])
