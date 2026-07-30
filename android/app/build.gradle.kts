plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("com.chaquo.python")
}

android {
    namespace = "com.lvjiang.app"
    compileSdk = 35

    defaultConfig {
        applicationId = "com.lvjiang.app"
        minSdk = 26
        targetSdk = 35
        versionCode = 2
        versionName = "0.1.0"
        // 设备为 arm64（vivo V2415A），Chaquopy 按 ABI 打包 Python 运行时
        ndk { abiFilters += listOf("arm64-v8a") }
    }

    buildFeatures {
        aidl = true
        buildConfig = true // AGP 8 默认关闭，ShellBridge 需要 BuildConfig.APPLICATION_ID
    }

    buildTypes {
        release {
            isMinifyEnabled = false
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
    }
}

// Chaquopy 用 buildPython 建一个 venv 来跑 pip，因此 pip 看到的解释器版本就是它的版本。
// 若沿用系统 Python 3.13，rapidocr_onnxruntime 的 Requires-Python (>=3.6,<3.13) 会把安装
// 直接拦下；版本对齐后还能顺带预编译 .pyc（否则设备首次导入明显变慢）。
// 该解释器由 `uv python install 3.10` 装入 .tooling（已 gitignore），与 jdk17/gradle 同为便携工具链。
val buildPythonExe = rootProject.file("../.tooling/python/cpython-3.10-windows-x86_64-none/python.exe")
if (!buildPythonExe.exists()) {
    throw GradleException(
        "缺少 buildPython：$buildPythonExe\n" +
            "请执行：\$env:UV_PYTHON_INSTALL_DIR=\"<repo>/.tooling/python\"; uv python install 3.10"
    )
}

chaquopy {
    defaultConfig {
        // 3.10 而非 3.11：Chaquopy 包仓库中 opencv-python 与 shapely 的最高 Android wheel
        // 均止于 cp310，而这两者是 OCR 链路的硬依赖。仓库 requires-python 为 >=3.10，
        // 且现有代码未使用 3.11+ 语法，故降级无需改动任何 Python 源码。
        version = "3.10"
        buildPython(buildPythonExe.absolutePath)
        // OCR 链路依赖。rapidocr_onnxruntime 本体是纯 Python 包，直接从 PyPI 装即可
        // （连带 15.8MB 模型与 config.yaml 一起进 APK），PC 端因此无需任何改动。
        //
        // 但它声明的三个依赖是原生扩展，Chaquopy 仓库里没有对应的 Android wheel：
        //   onnxruntime —— 推理改走 Kotlin 侧 OnnxBridge（onnxruntime-android）
        //   pyclipper   —— 仅 unclip 用到，改用 cv2.minAreaRect 等价实现
        //   shapely     —— 同为 unclip 专用（Polygon.area/length），改用 cv2.contourArea/arcLength
        // 这里给三者各装一个 pystubs/ 下的占位分发包，而不是全局关掉依赖解析：
        // numpy/opencv/Pillow 的 chaquopy-{openblas,libgfortran,libjpeg,libpng,freetype,libcxx}
        // 等原生库正是靠依赖解析自动拉取的，一旦 --no-deps 就会缺库并在运行时崩。
        // 占位包里的每个符号被真实调用时都会抛错，替换若未生效会立刻暴露而非静默走偏。
        // 上游声明的 six / tqdm 在 1.4.4 源码里实际未被 import，但依赖解析仍需满足，
        // 二者是纯 Python 包，照常从 PyPI 装。
        // 版本显式钉死：Chaquopy 仓库里每个包的可用版本有限，隐式解析容易挑到无 wheel 的版本。
        pip {
            install("rapidocr_onnxruntime==1.4.4")
            install("./pystubs/onnxruntime")
            install("./pystubs/pyclipper")
            install("./pystubs/shapely")
            install("numpy==1.26.2")
            install("opencv-python==4.5.1.48")
            install("Pillow==11.0.0")
            install("PyYAML==6.0.1")
            install("loguru==0.7.3")
            install("lark==1.3.1")
        }
    }

    // 仓库的 Python 源根。srcDir 是追加语义，默认的 src/main/python（hello.py 冒烟用）保留。
    //
    // 这里能直接指向仓库 src/ 而不必做一份「只含 .py 的镜像」，靠的是 Python 侧采用了
    // src-layout：源根 src/ 下只有 lvjiang/ 一个子项，于是打包边界是可枚举的。若包目录直接
    // 放在仓库根，srcDir 就只能指仓库根，进而把 .venv/ config/ data/ docs/ logs/ 全卷进 APK，
    // 只能靠 exclude 黑名单挡 —— 那样新增一个顶层目录的默认行为是「静默进包」而不是报错。
    sourceSets {
        getByName("main") {
            srcDir("../../src")
        }
    }
}

dependencies {
    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.appcompat:appcompat:1.7.0")
    implementation("com.google.android.material:material:1.12.0")
    // Shizuku：shell 权限通道（UserService 承载 input/screencap）
    implementation("dev.rikka.shizuku:api:13.1.5")
    implementation("dev.rikka.shizuku:provider:13.1.5")
    // ONNX Runtime：Phase 1 起承载 RapidOCR 同款模型推理
    implementation("com.microsoft.onnxruntime:onnxruntime-android:1.20.0")
}

// 系统配置（场景/工作流 YAML）随 APK 分发：从仓库 config/system 同步进 assets，
// App 启动时解压到 filesDir/lvjiang/config/system（见 App.kt）。
// 用 Sync 任务而非手拷一份：仓库里只有 config/system 一个数据源，不会两处失同步。
val syncSystemConfig = tasks.register<Sync>("syncSystemConfig") {
    from(rootProject.file("../config/system"))
    into(layout.buildDirectory.dir("generated/lvjiang_assets/config/system"))
}
// 布局文件（手机直控.json）随 APK 分发：从仓库 config/local/layouts 同步进 assets，
// App 启动时解压到 filesDir/lvjiang/config/local/layouts。
val syncLayoutConfig = tasks.register<Sync>("syncLayoutConfig") {
    from(rootProject.file("../config/local/layouts"))
    include("手机直控.json")
    into(layout.buildDirectory.dir("generated/lvjiang_assets/config/local/layouts"))
}
android.sourceSets["main"].assets.srcDir(layout.buildDirectory.dir("generated/lvjiang_assets"))
tasks.matching { it.name.startsWith("generate") && it.name.endsWith("Assets") }
    .configureEach { dependsOn(syncSystemConfig, syncLayoutConfig) }
