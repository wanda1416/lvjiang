# Release 签名说明

release 构建的签名配置从 `android/keystore.properties` 读取（见
`app/build.gradle.kts`）：文件存在则用正式 keystore 签名，不存在则走
debug 签名兜底——`assembleRelease` 始终能跑，但 debug 签名的产物不能
用于正式分发（换机安装会与正式签名版本冲突）。

## 文件与忽略规则

| 文件 | 说明 | 入库 |
|---|---|---|
| `android/lvjiang.jks` | 密钥库（RSA 2048，有效期 10000 天，别名 `lvjiang`） | ❌ `.gitignore` |
| `android/keystore.properties` | 路径与密码，Gradle 构建时读取 | ❌ `.gitignore` |
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
  `pm install -r` 会报签名不一致。
- 密码只存在于本机 `keystore.properties`，不入库、不写进任何文档或提交信息。
