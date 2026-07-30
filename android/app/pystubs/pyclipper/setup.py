"""pyclipper 占位包：仅用于满足 rapidocr_onnxruntime 的依赖声明。

真包是 Cython 包装的 C++ Clipper 库，属原生扩展，而 Chaquopy 包仓库
（chaquo.com/pypi-13.1）没有它的 Android wheel，装不上。

它在 rapidocr_onnxruntime 里只被 ch_ppocr_det/utils.py 的 DBPostProcess.unclip
使用，而该方法已由 src/lvjiang/core/ondevice/rapidocr_adapter.py 替换为 cv2 等价实现，
因此这里只需让顶层 import 与 pip 依赖解析通过。

版本号取 99.0.0 以满足上游的 pyclipper>=1.2.0 约束。
"""

from setuptools import setup

setup(name="pyclipper", version="99.0.0", py_modules=["pyclipper"])
