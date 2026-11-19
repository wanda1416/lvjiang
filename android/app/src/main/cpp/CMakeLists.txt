cmake_minimum_required(VERSION 3.22.1)
project(lvjiang_ocr LANGUAGES CXX)
set(CMAKE_CXX_STANDARD 17)
add_library(opencv UNKNOWN IMPORTED)
set_target_properties(opencv PROPERTIES
    IMPORTED_LOCATION "${OPENCV_ROOT}/jni/${ANDROID_ABI}/libopencv_java4.so")

# 对齐 APK 中的 ORT 1.20.0。头文件随仓库内置（third_party/onnxruntime），构建不联网：
# 以前在配置阶段从 raw.githubusercontent 下载，2026-11-19 起该地址返回非 2xx，
# Android Release 构建连带失败两次（见该目录 README）。哈希仍逐文件校验，防误改与漂移。
set(ORT_INCLUDE "${CMAKE_CURRENT_SOURCE_DIR}/third_party/onnxruntime")
function(ort_header NAME HASH)
    set(SRC "${ORT_INCLUDE}/${NAME}")
    if(NOT EXISTS "${SRC}")
        message(FATAL_ERROR "缺少内置 ORT 头文件: ${SRC}（见 third_party/onnxruntime/README.md）")
    endif()
    file(SHA256 "${SRC}" ACTUAL)
    if(NOT ACTUAL STREQUAL HASH)
        message(FATAL_ERROR "内置 ORT 头文件校验失败: ${NAME}（期望 ${HASH}，实际 ${ACTUAL}）")
    endif()
endfunction()
ort_header(onnxruntime_c_api.h 573a117ae6b83ead7f53da6cedaa0d7b7d9cdb45c0ba4726bac9081e67696a82)
ort_header(onnxruntime_cxx_api.h 1f5b8bcde589106bdf5f0e35dea25f8794bec1288d8aa83d875801b321cdf127)
ort_header(onnxruntime_cxx_inline.h 45cb911ad364e61c038e3f01d324b27b15871e0c7cc80435007e4d3f5a70c43a)
ort_header(onnxruntime_float16.h 6c4db3d1954b266c4febe413351af86cac922e3448538e7e452e51d646e56a4a)
add_library(ort UNKNOWN IMPORTED)
set_target_properties(ort PROPERTIES
    IMPORTED_LOCATION "${ORT_ROOT}/jni/${ANDROID_ABI}/libonnxruntime.so")
add_library(lvjiang_ocr SHARED native_ocr.cpp)
target_include_directories(lvjiang_ocr PRIVATE "${ORT_INCLUDE}"
    "${OPENCV_ROOT}/prefab/modules/opencv_java4/include")
target_link_libraries(lvjiang_ocr PRIVATE ort opencv)
target_compile_options(lvjiang_ocr PRIVATE -Wall -Wextra -Werror=return-type)
