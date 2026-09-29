# license-issuer：激活码签发器

签发者本机运行的小 GUI，签发律匠高级功能的离线激活码，并用主程序内置的公钥当场
自检——发出去之前就确认客户端一定收得下，而不是等用户回来说「激活码无效」。

## 定位

授权体系是**离线**的：用户机器算出序列号 → 你用私钥签一张激活码 → 程序用内置公钥
验签。没有服务器，没有联网校验。

它挡的是「把激活码转手发给别人」，**不挡逆向**——客户端校验天然可以被绕过，所以
整套实现保持明文可读，不做混淆、反调试或完整性自检。

> **现状**：主程序目前**没有任何功能接入门禁**（后台截图曾短暂挂在这里，现已直接
> 开放），整套链路是通的但暂时没有消费者。要把某个功能设为高级功能，在
> `src/lvjiang/core/license/__init__.py` 的 `KNOWN_FEATURES` 里登记，并在该功能的
> UI 入口与能力入口各校验一次 `has_feature()`。

## 目录构成

| 文件 | 作用 |
|---|---|
| `issuer.py` | 签发 GUI 入口（日常用这个） |
| `keygen.py` | 生成签发密钥对，一次性；私钥已存在时直接拒绝 |
| `license_issuer/signing.py` | 签发逻辑，与界面分离，可单测 |
| `license_issuer/app.py` | 界面 |

签发与验签的正文格式不在这里定义，直接复用主程序的
`src/lvjiang/core/license/code.py`——两处各写一遍，改了其中一处就会静默签出验不过
的码，而现象只是用户说「激活码无效」，两头都查不出来。

## 快速开始

**直接用仓库根目录的 `.venv`，不需要单独建环境**——依赖（PyQt6、cryptography）
主项目本来就有，签发用的正文格式也直接复用 `src/lvjiang/core/license`。

```powershell
# 仓库根目录执行
.venv\Scripts\python.exe ops\license-issuer\issuer.py      # Windows
.venv/bin/python ops/license-issuer/issuer.py                # macOS / Linux
```

首次使用需要先有私钥（本工具**不生成**私钥，见下）：

```powershell
.venv\Scripts\python.exe ops\license-issuer\keygen.py
```

它把私钥写到 `~/.lvjiang/license_signing_key.txt`，并打印一行公钥让你粘进
`src/lvjiang/core/license/code.py` 的 `PUBLIC_KEY_B32`。

## 两种激活码

| | 绑机码 | 免绑定码 |
|---|---|---|
| 正文里有序列号 | ✅ | ❌ |
| 用户要先提供什么 | 序列号（在「配置管理 → 功能激活」里复制） | 不需要 |
| 转发给别人 | 无效 | **有效** |
| 建议 | 常规发放用这种 | 发给特别用户，**务必设有效期** |

免绑定码本质是 bearer token，谁拿到谁能用。离线方案没有吊销手段，**有效期是唯一
的把手**——到期不续即可。工具在你没设有效期时会显眼提示。

## 为什么不做「生成私钥」按钮

生成私钥会覆盖旧私钥，而覆盖意味着**所有已签发的激活码一起失效，且不可逆**。这种
分量的操作不该放在随手点得到的按钮后面，所以留在命令行里；即便在命令行，
`keygen` 在目标文件已存在时也会直接拒绝，没有 `--force` 逃生口——真要轮换就自己
把旧文件备份移走，让这一步的分量由人来掂。

## 私钥放哪

默认 `~/.lvjiang/license_signing_key.txt`，**仓库之外**。

特意不放 `config/local/`：那是被跟踪的配置仓库，密钥搁进去迟早被误提交。

**务必备份。** 丢了就得换公钥重新发版，所有已签发的码一起失效。

另外：**不要复用 Android 的 `lvjiang.jks`**。那把钥匙丢了或泄漏，你就无法给已安装
用户发布升级包（见 `android/README-signing.md`）；签发许可要在日常办公机上反复
动用私钥，暴露面完全不同。一把钥匙只干一件事。

## 台账

工具界面底部提示记一笔「编号 → 发给谁 → 日期」。离线撤不掉一张已发出的码，但下次
可以不续——前提是你知道那张码是谁的。码编号写进激活码正文，用户报编号你就能查到。

## 测试

```bash
# 仓库根目录执行，用同一个 .venv
PYTHONPATH=ops/license-issuer .venv/bin/python -m pytest ops/license-issuer/tests -q
```

这些用例不在主仓库的 `pytest tests` 范围内（ops 下的工具与主程序分开跑），改动
签发逻辑后记得单独跑一次。

签发逻辑与界面分离（`signing.py` / `app.py`），测的是签发逻辑：每签一张都用主程序
的验签函数验回去，确保两端格式不会各走各的。
