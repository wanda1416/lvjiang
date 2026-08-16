# 燕云十六声官方 API 接口参考

> 关键词：API、access_token、装备数据、体力、心力、不肝、destiny.cool、网易

---

## 一、背景

燕云十六声（yysls）的第三方工具 destiny.cool（作者：七龙HHA）通过网易官方数据接口获取角色数据。本文档记录对该接口的完整逆向分析结果，为项目后续集成官方数据做准备。

---

## 二、API 基础信息

### 2.1 服务端点

```
https://s3.game.163.com/7540694694f2dddc/
```

- `7540694694f2dddc` 为硬编码的哈希前缀，所有请求必须携带；
- 该前缀来源于 destiny.cool 前端 JS bundle（`index-DD_jAavX.js`）。

### 2.2 认证方式

- Token 通过 **query parameter** 传递（非 Header）；
- 参数名：`access_token`；
- Token 格式：JWT（HS256），由游戏官方签发，有效期约 60 天。

```
GET /7540694694f2dddc/<endpoint>?access_token=<url_encoded_jwt>
```

### 2.3 Token 获取

- 来源：`config/session/users/<角色名>.json` 中的 `access_token` 字段；
- JWT 结构：`{"sub":"{\"auth\"...}","iat":...,"exp":...}`；
- 示例 token 签发时间 2026-08-06，过期时间 2026-10-05。

---

## 三、已知 API 端点

### 3.1 `/game/regional/data` — 角色完整数据

**最重要的接口**，返回角色的全部装备、属性、体力/心力等信息。

```
GET https://s3.game.163.com/7540694694f2dddc/game/regional/data?access_token={token}
```

#### 响应结构（`data` 字段）

| 字段 | 类型 | 说明 |
|------|------|------|
| `roleId` | string | 角色 ID，如 `"0339046679"` |
| `roleName` | string | 角色名，如 `"测试用户A"` |
| `level` | int | 等级（100） |
| `kongfuMain` | int | 主功法 ID（10102 = 牵丝霖） |
| `kongfuSub` | int | 副功法 ID（10202） |
| `school` | int | 门派 ID |
| `clubId` | string | 帮派 ID |
| `clubName` | string | 帮派名 |
| `loginTime` | int | 最后登录时间戳 |
| `onlineTime` | int | 在线时长（秒） |
| `createTime` | int | 角色创建时间戳 |
| `fashionScore` | int | 外观评分 |
| `maxXiuWeiKungFu` | int | 最高修为 |
| `xiuWeiKungFu` | int | 当前修为 |
| `wearEquips` | dict | 部位编号 → 装备编号映射 |
| `wearEquipsDetailed` | dict | 部位编号 → 装备完整详情 |
| `merge1Vo` | dict | **体力/心力/不肝数据** |
| `roleBaseInfoVo` | dict | 角色基础信息（创建日期、难度等） |
| `fiveDimVo` | dict | 五维属性（力/敏/体/根/精） |
| `xiuWeiAttrV2Vo` | dict | 修为面板属性（攻击/防御/命中/暴击等） |
| `damageIncreaseVo` | dict | 伤害加成详情 |
| `xinfaInfo` | dict | 心法信息 |
| `passiveSlots` | list | 被动技能槽 |
| `battleQs` | dict | 战斗快捷设置 |
| `exploreThirdRegionCollect` | dict | 探索收集数据 |

#### 体力/心力/不肝数据（`merge1Vo`）

```json
{
  "tiliMaxVal": 2500,
  "tiliVal": 1855,
  "xinliVal": 468,
  "xinliMaxVal": 600,
  "xinliLastTime": null,
  "buganVal": 23000,
  "buganLastTime": "2026-08-06 09:24:30"
}
```

| 字段 | 说明 |
|------|------|
| `tiliVal` | 当前体力 |
| `tiliMaxVal` | 体力上限 |
| `xinliVal` | 当前心力 |
| `xinliMaxVal` | 心力上限 |
| `xinliLastTime` | 心力最后恢复时间（null 表示已满） |
| `buganVal` | 不肝值 |
| `buganLastTime` | 不肝值最后更新时间 |

#### 装备详情（`wearEquipsDetailed`）

每个部位包含：

```json
{
  "no": "1101972",
  "exVo": {
    "toneExp": 780,
    "toneDeterMin": 310002,
    "retoned": 4,
    "durability": 17,
    "suffix": 2,
    "baseAttrs": {
      "MIN_W_ATK": 100,
      "MAX_W_ATK": 232
    },
    "baseAffixes": [
      {
        "equipmentDetails": [属性ID, 数值, cap百分比, 品质, 是否承音]
      }
    ]
  }
}
```

- `baseAttrs`：基础属性（因部位而异：武器有 MIN/MAX_W_ATK，防具有 W_DEF/HP_MAX 等）；
- `baseAffixes`：词条数组，每条为 `equipmentDetails` 五元组；
- `retoned`：已调律次数；
- `toneExp`：调律经验；
- `toneDeterMin`：定音词条 ID；
- 最后一条 `baseAffixes` 为定音词条。

#### 部位编号对照

| 编号 | 部位 |
|------|------|
| 1 | 主武器 |
| 2 | 副武器 |
| 3 | 冠胄 |
| 4 | 胸甲 |
| 5 | 胫甲 |
| 8 | 腕甲 |
| 9 | 环 |
| 10 | 佩 |
| 11 | 特殊（如背部装饰） |
| 21 | 特殊（如腰饰） |

### 3.2 `/hun/basic` — 帮派信息

```
GET https://s3.game.163.com/7540694694f2dddc/hun/basic?access_token={token}
```

返回帮派名称、等级、成员列表（含每个成员的 roleId、等级、功法、在线状态、职位等）。

### 3.3 `/hun/customize/info` — 活动时间表

```
GET https://s3.game.163.com/7540694694f2dddc/hun/customize/info?access_token={token}
```

返回帮派活动时间配置（派对时间、通用活动时间、战斗活动时间等）。

### 3.4 `/hun/station` — 排名数据

```
GET https://s3.game.163.com/7540694694f2dddc/hun/station?access_token={token}
```

返回帮派排名和成员信息。

---

## 四、响应格式

所有端点返回统一 JSON 格式：

```json
{
  "status": true,
  "success": true,
  "code": 200,
  "msg": "成功",
  "data": { ... }
}
```

错误时（如服务器维护）：

```json
{
  "status": false,
  "code": 600,
  "msg": "服务器升级中"
}
```

---

## 五、调用示例（Python）

```python
import json
import urllib.request
import urllib.parse

# 读取 token
with open("config/session/users/测试用户A.json", "r", encoding="utf-8") as f:
    token = json.load(f)["access_token"]

HASH = "7540694694f2dddc"
BASE = f"https://s3.game.163.com/{HASH}"
tok = urllib.parse.quote(token, safe="")

# 获取角色完整数据
url = f"{BASE}/game/regional/data?access_token={tok}"
req = urllib.request.Request(url, method="GET")
req.add_header("User-Agent", "Mozilla/5.0")
with urllib.request.urlopen(req, timeout=15) as resp:
    result = json.loads(resp.read().decode("utf-8"))

data = result["data"]

# 体力/心力/不肝
stamina = data["merge1Vo"]
print(f"体力: {stamina['tiliVal']}/{stamina['tiliMaxVal']}")
print(f"心力: {stamina['xinliVal']}/{stamina['xinliMaxVal']}")
print(f"不肝: {stamina['buganVal']}")

# 装备
for pos, detail in data["wearEquipsDetailed"].items():
    ex = detail["exVo"]
    print(f"部位 {pos}: {ex['baseAttrs']}, 调律 {ex['retoned']} 次")
```

---

## 六、数据来源与验证

- API URL 格式通过逆向 destiny.cool 的 JS bundle 发现（`index-DD_jAavX.js`）；
- 关键代码位置：
  - `Ds` 变量（约 L75756）：`regional/data` URL 模板；
  - 百业加载代码（约 L112430）：`Promise.all` 中并行请求 3 个 `/hun/` 端点；
- destiny.cool 声明：除网易官方接口外，不会向其他服务器发送携带 token 的请求；
- 全部 4 个端点已于 2026-08-07 测试通过，返回 `code: 200`。

---

## 七、后续集成方向

1. **装备数据解析**：将 `wearEquipsDetailed` 中的原始属性 ID 映射为可读属性名（需下载 destiny.cool 的 `equip/decoded/equip_name.json` 等映射文件）；
2. **体力/心力监控**：基于 `merge1Vo` 实现日常体力管理提醒；
3. **WASM 计算对接**：将 API 返回的装备数据输入 `yysls_calc.wasm` 进行面板计算和毕业率评估；
4. **多角色支持**：遍历 `config/session/users/` 下所有角色 JSON，批量拉取数据。
