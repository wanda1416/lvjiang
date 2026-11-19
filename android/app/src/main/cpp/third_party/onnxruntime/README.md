# ONNX Runtime 头文件（随仓库内置）

来自 **microsoft/onnxruntime v1.20.0**（MIT License）的公开 C/C++ API 头文件，
与 APK 里打包的 `libonnxruntime.so` 版本对齐。

| 文件 | SHA-256 |
|---|---|
| `onnxruntime_c_api.h` | `573a117ae6b83ead7f53da6cedaa0d7b7d9cdb45c0ba4726bac9081e67696a82` |
| `onnxruntime_cxx_api.h` | `1f5b8bcde589106bdf5f0e35dea25f8794bec1288d8aa83d875801b321cdf127` |
| `onnxruntime_cxx_inline.h` | `45cb911ad364e61c038e3f01d324b27b15871e0c7cc80435007e4d3f5a70c43a` |
| `onnxruntime_float16.h` | `6c4db3d1954b266c4febe413351af86cac922e3448538e7e452e51d646e56a4a` |

## 为什么内置而不是构建时下载

以前 `CMakeLists.txt` 在 CMake 配置阶段用 `file(DOWNLOAD)` 从
`raw.githubusercontent.com/microsoft/onnxruntime/v1.20.0/...` 拉这四个头，并用
`EXPECTED_HASH` 钉住内容。2026-11-19 的 0.15.18 发布里，该地址开始返回非 2xx
（CMake 报 `file DOWNLOAD HASH mismatch`、`actual hash: e3b0c442…`（空串）、
`status: 22;"HTTP response code said error"`），Android Release 构建连带失败两次——
**发布流程被一个外部 HTTP 端点卡住**。

内置之后：构建不再联网，哈希仍由 `CMakeLists.txt` 校验（防误改），升级 ORT 时
四个文件与 `libonnxruntime` 的版本一起改，并同步更新本表的哈希。

## 升级步骤

1. 从与目标 ORT 版本一致的源取这四个头（本地 `onnxruntime` wheel 不含头文件，
   可用同版本 AAR 解包，或从上次成功构建的 `android/app/.cxx/**/ort-include/` 里取）；
2. 覆盖本目录文件，更新上表与 `CMakeLists.txt` 里的 `SHA256=` 钉值；
3. `grep -n "ORT" android/app/src/main/cpp/CMakeLists.txt` 确认没有残留的下载分支。
