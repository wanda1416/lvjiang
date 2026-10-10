import java.util.Properties

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("com.chaquo.python")
}

// C++ 与 Java 复用同一份 ORT AAR，不另打包一个推理运行库。
val nativeOrt by configurations.creating
val prepareNativeOrt by tasks.registering(Sync::class) {
    from({ zipTree(nativeOrt.singleFile) }) { include("jni/**") }
    into(layout.buildDirectory.dir("native-ort"))
}
val nativeOpenCv by configurations.creating
val prepareNativeOpenCv by tasks.registering(Sync::class) {
    from({ zipTree(nativeOpenCv.singleFile) }) {
        include("prefab/modules/opencv_java4/include/**", "jni/**/libopencv_java4.so")
    }
    into(layout.buildDirectory.dir("native-opencv"))
}

android {
    namespace = "com.lvjiang.app"
    compileSdk = 35
    ndkVersion = "26.1.10909125"

    defaultConfig {
        applicationId = providers.gradleProperty("lvjiangTestApplicationId").orElse("com.lvjiang.app").get()
        manifestPlaceholders["lvjiangLabel"] = if (applicationId == "com.lvjiang.app") "@string/app_name" else "律匠离线验收"
        minSdk = 26
        targetSdk = 35
        // 应用版本仅在发布时递增；预置配置以内容摘要更新，不提升 content_version。
        versionCode = 63
        versionName = "0.13.15"
        // 正式默认仍为 arm64；软件模拟器验收可显式 -PlvjiangAbi=x86_64。
        ndk { abiFilters += listOf(providers.gradleProperty("lvjiangAbi").orElse("arm64-v8a").get()) }
        externalNativeBuild {
            cmake {
                arguments += "-DORT_ROOT=${layout.buildDirectory.dir("native-ort").get().asFile.absolutePath}"
                arguments += "-DANDROID_STL=c++_shared"
                arguments += "-DOPENCV_ROOT=${layout.buildDirectory.dir("native-opencv").get().asFile.absolutePath}"
            }
        }
    }

    buildFeatures {
        aidl = true
        buildConfig = true // AGP 8 默认关闭，ShellBridge 需要 BuildConfig.APPLICATION_ID
    }

    // 签名配置：从 android/keystore.properties 读取。
    //
    // **缺密钥时不再退回 debug 签名。** 那个兜底在本机很省事，但在 CI 上是陷阱：
    // 构建照样成功，产出的却是签名错的 APK，装到用户机器上与正式版冲突，而且
    // 没有任何一步会报错。所以 release 构建没有有效 keystore 就直接失败。
    // debug 构建不受影响（DEPLOYMENT.md 的主流程用 assembleDebug，不需要密钥）。
    val ksPropsFile = rootProject.file("keystore.properties")
    if (ksPropsFile.exists()) {
        val ksProps = Properties()
        ksPropsFile.inputStream().use { ksProps.load(it) }
        signingConfigs {
            create("release") {
                storeFile = rootProject.file(ksProps.getProperty("storeFile"))
                storePassword = ksProps.getProperty("storePassword")
                keyAlias = ksProps.getProperty("keyAlias")
                keyPassword = ksProps.getProperty("keyPassword")
            }
        }
    }

    buildTypes {
        release {
            // R8 代码缩减 + 资源压缩：debug 77MB → 预期 40-50MB
            // 主要收益：移除未用 Kotlin 字节码、压缩 assets、合并重复资源
            isMinifyEnabled = true
            isShrinkResources = true
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro",
            )
            // 没有正式 keystore 时留空而不是退回 debug：宁可产出未签名包被后续
            // 步骤拦下，也不要产出一个"看起来能装"的错签名包
            signingConfig = if (ksPropsFile.exists()) {
                signingConfigs.getByName("release")
            } else {
                null
            }
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
    }
    sourceSets.getByName("main").jniLibs.srcDir(layout.buildDirectory.dir("native-opencv/jni"))
    externalNativeBuild {
        cmake {
            path = file("src/main/cpp/CMakeLists.txt")
            version = "3.22.1"
        }
    }
}

tasks.configureEach {
    if (name == "preBuild" || name.startsWith("configureCMake") || name.startsWith("buildCMake")) {
        dependsOn(prepareNativeOrt, prepareNativeOpenCv)
    }
}
// Chaquopy 用 buildPython 建一个 venv 来跑 pip，因此 pip 看到的解释器版本就是它的版本。
// 若沿用系统 Python 3.13，rapidocr_onnxruntime 的 Requires-Python (>=3.6,<3.13) 会把安装
// 直接拦下；版本对齐后还能顺带预编译 .pyc（否则设备首次导入明显变慢）。
// 该解释器由 `uv python install 3.10` 装入 .tooling（已 gitignore），与 jdk17/gradle 同为便携工具链；
// Windows 与 macOS/Linux 的目录名和可执行文件位置不同，这里按平台通配解析，也允许
// `-PbuildPython=<path>` 或环境变量 LVJIANG_BUILD_PYTHON 直接指定。
val buildPythonExe: File = run {
    val explicit = (project.findProperty("buildPython") as String?) ?: System.getenv("LVJIANG_BUILD_PYTHON")
    if (!explicit.isNullOrBlank()) return@run File(explicit)
    val root = rootProject.file("../.tooling/python")
    val isWindows = System.getProperty("os.name").lowercase().contains("win")
    val candidates = root.listFiles { f -> f.isDirectory && f.name.startsWith("cpython-3.10") }
        ?.sortedBy { it.name }
        ?.map { if (isWindows) File(it, "python.exe") else File(it, "bin/python3") }
        ?.filter { it.exists() }
        .orEmpty()
    candidates.firstOrNull() ?: File(root, if (isWindows) "cpython-3.10-windows-x86_64-none/python.exe" else "cpython-3.10-<platform>/bin/python3")
}
if (!buildPythonExe.exists()) {
    throw GradleException(
        "缺少 buildPython：$buildPythonExe\n" +
            "请执行：UV_PYTHON_INSTALL_DIR=<repo>/.tooling/python uv python install 3.10\n" +
            "（PowerShell：\$env:UV_PYTHON_INSTALL_DIR=\"<repo>/.tooling/python\"; uv python install 3.10）\n" +
            "或用 -PbuildPython=<python 可执行文件> / 环境变量 LVJIANG_BUILD_PYTHON 指定"
    )
}

// release 任务没有有效签名就不许开工。放在配置阶段而不是打包阶段：等 R8 和
// Chaquopy 跑完十几分钟再报"没密钥"毫无意义。
// 只看本次调用的任务名，所以 assembleDebug 完全不受影响。
run {
    val wantsRelease = gradle.startParameter.taskNames.any { it.contains("Release") }
    val keystoreProps = rootProject.file("keystore.properties")
    if (wantsRelease && !keystoreProps.exists()) {
        throw GradleException(
            "release 构建需要正式签名，但找不到 $keystoreProps\n" +
                "本机发布：按 android/README-signing.md 生成 keystore 并写入该文件\n" +
                "CI：确认 ANDROID_KEYSTORE_BASE64 等四个 secret 已配置（见同一文档）\n" +
                "只是想装到手机上测试请用 assembleDebug，它不需要密钥"
        )
    }
}

chaquopy {
    defaultConfig {
        // 3.10 而非 3.11：Chaquopy 包仓库中 opencv-python 与 shapely 的最高 Android wheel
        // 均止于 cp310，而这两者是 OCR 链路的硬依赖。桌面发行包声明 Python >=3.11
        // 是为了 Windows 高精度 sleep；APK 不通过 pip 安装本项目，而是由 Chaquopy
        // 直接打包 src，因此设备端会加载的源码仍须保持 Python 3.10 兼容。
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
            install("fasteners==0.20")
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
    nativeOrt("com.microsoft.onnxruntime:onnxruntime-android:1.20.0@aar")
    nativeOpenCv("org.opencv:opencv:4.10.0@aar")
}

// 预置只从官方 system 生成，绝不打包个人 local/remote/session 或 DB。
val presetAssets = layout.buildDirectory.dir("generated/systemPreset/assets")
val generateSystemPreset by tasks.registering(Exec::class) {
    inputs.dir(rootProject.file("../config/system"))
    inputs.file(rootProject.file("../src/lvjiang/_version.py"))
    inputs.file(rootProject.file("../src/lvjiang/core/system_preset.py"))
    inputs.file(rootProject.file("../scripts/build_android_preset.py"))
    outputs.file(presetAssets.map { it.file("lvjiang-preset.zip") })
    commandLine(buildPythonExe.absolutePath, rootProject.file("../scripts/build_android_preset.py").absolutePath,
        presetAssets.get().file("lvjiang-preset.zip").asFile.absolutePath)
}
android.sourceSets.getByName("main").assets.srcDir(presetAssets)
tasks.named("preBuild").configure { dependsOn(generateSystemPreset) }
