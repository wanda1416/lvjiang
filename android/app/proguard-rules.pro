# ─── Chaquopy Python ↔ Java 桥接 ─────────────────────────────
# Chaquopy 生成的规则已自动保留 com.chaquo.python.** 与 Kotlin 函数接口，
# 这里只补应用侧：Python 通过 Chaquopy 反射访问的 Kotlin object / 类必须保留。

# Kotlin object 的 INSTANCE 字段：Python 侧通过 .INSTANCE 拿到单例
-keepclassmembers class com.lvjiang.app.A11yBridge {
    public static final com.lvjiang.app.A11yBridge INSTANCE;
}
-keepclassmembers class com.lvjiang.app.ShellBridge {
    public static final com.lvjiang.app.ShellBridge INSTANCE;
}

# A11yBridge 的公开方法（Python 侧调用 .takeScreenshot / .dispatchClick 等）
-keep class com.lvjiang.app.A11yBridge {
    public *;
}
# ShellBridge：shell.py 反射导入（from com.lvjiang.app import ShellBridge）。
# 类名被 R8 混淆后 Chaquopy 直接报 No module named 'com'（e2e 实机踩到）
-keep class com.lvjiang.app.ShellBridge {
    public *;
}
# OnnxBridge 的公开方法（Python 侧调用 .run / .metadata / .close）
-keep class com.lvjiang.app.OnnxBridge {
    public *;
}
# OnnxOutput：run() 的返回类，Python 侧读 .data / .shape 字段。
# 只 keep 方法不 keep 字段时 R8 会把它砍成 'q' object has no attribute 'data'（e2e 实机踩到）
-keep class com.lvjiang.app.OnnxOutput {
    public *;
    <fields>;
}

# PyBridge：Kotlin 调 Python 的入口，Chaquopy 反射找 com.chaquo.python.Python.start
# 本身已被 Chaquopy 规则覆盖；但 PyBridge 自己的方法名（ensureStarted / call）
# 在 Kotlin 侧被显式引用，R8 能看到，不需要额外 keep。

# ─── 无障碍服务 / 悬浮服务 ───────────────────────────────────
# 系统通过 manifest 反射实例化，R8 一般能识别；显式保留以防万一。
-keep class com.lvjiang.app.A11yService {
    public <init>();
}
-keep class com.lvjiang.app.FloatService {
    public <init>();
}

# ─── Shizuku AIDL ────────────────────────────────────────────
# AIDL 生成的 Stub/Proxy 类被 ServiceConnection 反射引用
-keep class com.lvjiang.app.IShellService$* { *; }

# ─── ONNX Runtime ────────────────────────────────────────────
# onnxruntime-android 内部 JNI 调用，R8 默认保留；显式 keep 兜底
-keep class ai.onnxruntime.** { *; }
-dontwarn ai.onnxruntime.**

# ─── Chaquopy 自带规则已覆盖 ─────────────────────────────────
# - com.chaquo.python.**
# - kotlin.jvm.functions.** / kotlin.jvm.internal.FunctionBase
# - kotlin.reflect.KAnnotatedElement
# 这里不再重复声明。

-dontwarn org.jetbrains.annotations.NotNull
