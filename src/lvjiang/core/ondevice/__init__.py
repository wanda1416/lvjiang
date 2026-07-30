"""设备端专用模块 — 运行在 Android 应用进程内的代码

与 `src/lvjiang/core/android/` 的区别：那边是「PC 经 adb 控制手机」的客户端，运行在 PC 上；
这里是「代码本身跑在手机上」时才需要的适配层。

模块结构：
- rapidocr_adapter.py : 把 rapidocr_onnxruntime 的 pyclipper/shapely/onnxruntime
                        依赖点替换为设备端可用的实现
"""
