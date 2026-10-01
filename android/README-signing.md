# Release 签名说明

release 构建的签名配置从 `android/keystore.properties` 读取（见
`app/build.gradle.kts`）。**没有这个文件，release 构建直接失败**，不再退回
debug 签名——那个兜底在本机省事，在 CI 上是陷阱：构建照样成功，产出的却是签名错的
APK，装到用户机器上与正式版冲突，而且没有任何一步会报错。

debug 构建不需要密钥，`assembleDebug` 照常可用（`DEPLOYMENT.md` 的主流程就是它）。

## 文件与忽略规则

| 文件 | 说明 | 入库 |
|---|---|---|
| `android/lvjiang.jks` | 密钥库（RSA 2048，有效期 10000 天，别名 `lvjiang`） | ❌ `.gitignore` |
| `android/keystore.properties` | 路径与密码，Gradle 构建时读取 | ❌ `.gitignore` |
| `android/release-cert-sha256.txt` | 正式证书 SHA-256 指纹，发布流水线据此核验签名人 | ✅ |
| `android/README-signing.md` | 本文档 | ✅ |

`keystore.properties` 的四个键（`storeFile` 相对 `android/` 解析）：

```properties
storeFile=lvjiang.jks
storePassword=<密码>
keyAlias=lvjiang
keyPassword=<密码>
```

## 重新生成 keystore（丢失或换密钥时）

```powershell
# 仓库根目录执行；密码换成自己的，并同步更新 keystore.properties
.tooling\jdk17\bin\keytool.exe -genkeypair -v `
  -keystore android\lvjiang.jks -keyalg RSA -keysize 2048 -validity 10000 `
  -alias lvjiang -storepass <密码> -keypass <密码> `
  -dname "CN=lvjiang, OU=dev, O=lvjiang, C=CN"
```

## 注意事项

- **备份 `lvjiang.jks` 与密码**：Android 按签名识别应用，keystore 丢失后
  无法对已安装用户发布升级包，只能卸载重装。
- 签名变更（debug ↔ 正式，或换 keystore）后设备上必须先卸载旧包再装新包，
  `pm install -r` 会报签名不一致。换 keystore 还要同步更新
  `release-cert-sha256.txt`，否则发布流水线的签名核验会拦下新包。
- 密码只存在于本机 `keystore.properties`，不入库、不写进任何文档或提交信息。

## GitHub Actions 的签名材料

发布流水线（`.github/workflows/release.yml` 的 `build-android` 作业）在 **public
仓库**（`wanda1416/lvjiang`）上运行——标签推上去触发的是那边的工作流，所以 secret
要配在那个仓库的 Settings → Secrets and variables → Actions：

| Secret | 内容 |
|---|---|
| `ANDROID_KEYSTORE_BASE64` | `lvjiang.jks` 的 base64 |
| `ANDROID_KEYSTORE_PASSWORD` | `storePassword` |
| `ANDROID_KEY_ALIAS` | `lvjiang` |
| `ANDROID_KEY_PASSWORD` | `keyPassword` |

生成 base64：

```powershell
# Windows
[Convert]::ToBase64String([IO.File]::ReadAllBytes("android\lvjiang.jks")) | Set-Clipboard
```

```bash
# macOS / Linux
base64 -w0 android/lvjiang.jks
```

工作流把它解码成 `android/lvjiang.jks` 并按上面四个键写出 `keystore.properties`。
解码后的文件只存在于该次运行的工作目录，不进任何 artifact。缺 `ANDROID_KEYSTORE_BASE64`
时作业**立即失败**并指向本文档，不会继续构建出一个签不了名的包。

### 签名门禁

`build-android` 在打完包后独立核一次签名，构建侧的配置不作为唯一依据——签名是
「错了也能装、装了才冲突」的那类问题，发布前这是最后一道闸：

1. **必须不是 debug 签名**。debug 证书的 DN 固定为 `CN=Android Debug`，直接认它。
2. **指纹钉死**。`android/release-cert-sha256.txt` 里是正式证书的 SHA-256，APK 的
   签名证书必须与它一致。只验「非 debug」挡不住"签错了另一把钥匙"，这条才是真的锁。
   文件缺失时降级为只做第 1 条，并在日志里留一条 warning。

证书指纹不是机密——它嵌在每个已发布的 APK 里，任何人都能从安装包读出来，所以放仓库
比塞进 secret 更容易核对。取值：

```bash
keytool -list -v -keystore android/lvjiang.jks -alias lvjiang
```

取输出里 `SHA256:` 那一行。冒号与大小写无所谓，流水线会归一化；以 `#` 开头的注释行
会被跳过。
