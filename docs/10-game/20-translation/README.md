# 游戏术语中英对照（20-translation）

本目录整理《燕云十六声》国际服（Where Winds Meet）官方翻译中与律匠相关的
游戏独有术语，供识别映射、文档写作和与海外用户交流时统一口径。

## 数据来源

本目录的英文名来自两份不同性质的数据，作用不同，不能混用。

### 1. 国际服客户端翻译文件（游戏内全量文案，权威源）

国际服（Steam 版）把全部游戏内文案放在客户端目录下，每种语言一个容器文件：

```
<游戏安装目录>\Package\HD\oversea\locale\translate_words_map_<lang>
<游戏安装目录>\Package\HD\oversea\locale\translate_words_map_<lang>_diff
```

本机实际路径（Steam 库位置因机器而异）：

```
E:\SteamLibrary\steamapps\common\Where Winds Meet\Package\HD\oversea\locale\
```

`Package\HD` 下只有 `oversea` 和 `model` 两个目录——这是国际服客户端；
国服无此目录结构，本机也未安装国服客户端。

12 种语言 × （基线 + `_diff` 补丁增量）= 24 个文件，合计约 636 MB：

| 语言 | 基线大小 | 基线块数 | diff 大小 | diff 块数 |
|---|---|---|---|---|
| zh_cn（源语言） | 66.3 MB | 7097 | 0.2 MB | 2201 |
| en | 48.2 MB | 4387 | 0.7 MB | 3741 |
| zh_tw | 45.5 MB | 4370 | 0.3 MB | 2961 |
| ja | 50.8 MB | 4367 | 0.8 MB | 3267 |
| ko | 50.7 MB | 4370 | 0.5 MB | 3351 |
| th | 55.0 MB | 4333 | 0.5 MB | 3116 |
| vi | 51.4 MB | 4333 | 5.4 MB | 4333 |
| de | 50.7 MB | 4211 | 0.8 MB | 3645 |
| fr | 51.0 MB | 4211 | 0.8 MB | 3637 |
| pt_br | 49.2 MB | 4211 | 0.8 MB | 3677 |
| es | 49.9 MB | 4210 | 0.7 MB | 3613 |
| ru | 54.9 MB | 4178 | 0.9 MB | 3605 |

主文件是上线基线，`_diff` 是后续补丁追加的词条（内容示例：
`Glint Cloudshade: Trial`、`New Rewards for the Stream Rites Event`）。

此外还有一份启动器级别的明文文案（管理员权限、内存不足等 14 条提示），
只有 `zh_cn / en / zh_tw / ja / ko` 五种语言，与游戏内文案不是同一套体系：

```
<游戏安装目录>\Package\Other\jsonMultiLangSystemData.json
```

### 2. where-builds-meet 术语表（本目录实际采用的词表来源）

- 项目地址：<https://github.com/greydust/where-builds-meet>（GPL-3.0-or-later）
- 数据文件：`locales/translations.csv`，1081 条词条
- 该表即律匠 i18n 体系（`config/i18n/apps/yysls/`）此前采用的官方词表来源

列名有 12 种语言，但其中 9 列是建好却全空的占位列，实际填充率：

| 列 | 条数 | 占比 |
|---|---|---|
| en | 1081 | 100% |
| zh-Hant | 965 | 89% |
| ko | 726 | 67% |
| zh-Hans / ja / es / fr / de / pt / vi / ar / ru | 0 | 0% |

该项目自身界面也只提供 3 种语言（`public/locales/manifest.json`：
`en` / `zh-Hant` / `ko`，完成度 100% / 89% / 67%），其余语言的 json 是空对象。

因此本目录的中文列以繁中为基准转换；繁中缺失的约 11% 条目按国服叫法与
`docs/10-game` 各机制文档推定，属于人工核对结果而非官方数据。

## 客户端翻译文件的容器格式

`translate_words_map_*` 是无加密的 zstd 容器，全部 24 个文件都按同一布局
逐块校验过，块区精确铺满到文件末尾（无残留、无填充）。以 `en` 为例：

```
偏移 0        : EF BE AD DE            魔数 0xDEADBEEF（小端）
偏移 4        : u32 版本 = 1
偏移 8        : u32 块数                 en = 4387，zh_cn = 7097
偏移 12       : u32 保留 = 0
偏移 16       : u32[块数] 累积结束偏移   相对块区起点的累计长度，用于随机定位
偏移 16+块数*4 : 块区，每块 9 字节头 + 一帧 zstd
                u8   flag = 0x04
                u32  compressed_len
                u32  uncompressed_len
                zstd frame（精确 compressed_len 字节，帧头 28 B5 2F FD）
```

`en` 实测：块区压缩 50,493,132 字节，解压 94,599,472 字节（约 94 MB 文本）。

### 解码方式

解压是标准 zstd，不需要任何密钥；项目 venv 已带 `pyzstd`：

```python
import struct

import pyzstd

MAGIC = b"\xef\xbe\xad\xde"


def iter_blocks(path):
    """按块顺序产出解压后的字节，逐块内存占用约 20 KB。"""
    data = open(path, "rb").read()
    if data[:4] != MAGIC:
        raise ValueError("不是 translate_words_map 容器")
    count = struct.unpack("<I", data[8:12])[0]
    pos = 16 + count * 4          # 跳过 u32[count] 累积偏移索引
    for _ in range(count):
        clen = struct.unpack("<I", data[pos + 1:pos + 5])[0]
        frame = data[pos + 9:pos + 9 + clen]
        if frame[:4] != b"\x28\xb5\x2f\xfd":
            raise ValueError(f"偏移 {pos} 处不是 zstd 帧")
        yield pyzstd.decompress(frame)
        pos += 9 + clen
```

需要随机访问某一块时，用索引表定位：
`block_region_start + (index[i - 1] if i else 0)`，其中
`block_region_start = 16 + count * 4`。

### 解压后的内容形态

块内容不是按行或按 JSON 组织的词条表，而是**首尾相接、无分隔符的字符串池**：

```
The shopkeeper left, saying goodbye to life (send event).You are in auto-bidding mode.You…
```

第一个数据块解压后是纯 u32 数组（编号/偏移类索引），不含可读文本。也就是说
完整切分词条要结合该索引还原，目前仅验证到「块内容为连续文本」这一步，
尚未实现切分器——引用具体词条时不要假定块边界等于词条边界。

## 键结构

下表描述 where-builds-meet `translations.csv` 的键前缀（客户端容器文件没有
这样的文本键，只有编号索引）：

| 前缀 | 含义 | 收录位置 |
|---|---|---|
| `stat.*` | 战斗属性/词条 | [01-combat-stats.md](01-combat-stats.md) |
| `system.damageType.*` | 伤害类型 | 01 |
| `system.resource.*` | 战斗资源 | 01 |
| `system.martialArt.*` / `data.martialArt.*.talent.*` | 武学与武学天赋 | [02-martial-arts.md](02-martial-arts.md) |
| `system.weapon.*` | 武器类型 | 02 |
| `system.innerWay.*` / `data.innerway.*` | 心法 | [03-innerway.md](03-innerway.md) |
| `data.skill.*` | 技能（通用/奇术/各武学） | [04-skills.md](04-skills.md) |
| `data.dot.*` | 持续伤害 | 04 |
| `data.buff.*` / `data.debuff.*` | 增益/减益 | [05-buffs-debuffs.md](05-buffs-debuffs.md) |
| `system.gearSlot / gearRarity / gearSet`、`data.gearSet / armorSet / arsenal / bowRingSet` | 装备体系 | [06-equipment.md](06-equipment.md) |
| `system.attunement.*` | 定音词条 | 06 |
| `data.divinecraft.*` | 神工 | [07-consumables-misc.md](07-consumables-misc.md) |
| `data.script.*` | 帖 | 07 |
| `data.food.*` / `data.breakthrough.*` | 食物、突破 | 07 |
| `system.path.*` / `data.path.*` | 流派第二字 | 01、02 均有索引 |
| `ui.*`、`data.rotation.*`、`data.build.*` | where-builds-meet 自身界面/玩法配置 | **不收录**（非游戏术语） |

## 使用原则

1. **官方译名优先**。表内 `en` 列来自游戏内文本，写英文文档、回复海外用户时
   以它为准；不要自造直译。
2. **国服叫法以本仓库机制文档为准**。流派（裂石·威）、调律、定音等机制词的
   权威定义在 `docs/10-game` 各文档；本目录只做中英对照，不重新定义机制。
3. 表内个别 `en` 列存在拼写瑕疵（如 `penetraion`）或社区工具自造的缩写
   （`shortName`，如 `WTS`、`EDP`），引用时注意区分正式名与缩写。
4. 少数条目繁中列为空（多为 buff/debuff 描述与天赋名），对照表只收有中文
   依据的条目；纯英文无对照的技能变体（如 `1-Hit`/`[Cancel]` 分段）按规则
   标注而非逐条翻译。
5. 两份数据源词条范围不同：客户端容器是游戏内全量文案（解压约 94 MB），
   where-builds-meet 是配装工具整理的术语子集。要核实某条译名是否官方口径，
   最终应以客户端容器的解压文本为准。

## 文件索引

| 文件 | 内容 |
|---|---|
| [01-combat-stats.md](01-combat-stats.md) | 战斗属性、伤害类型、流派第二字、资源 |
| [02-martial-arts.md](02-martial-arts.md) | 武学、武器类型、武学天赋 |
| [03-innerway.md](03-innerway.md) | 心法 |
| [04-skills.md](04-skills.md) | 技能与持续伤害 |
| [05-buffs-debuffs.md](05-buffs-debuffs.md) | 增益/减益 |
| [06-equipment.md](06-equipment.md) | 装备部位/品阶/套装/弓玦、定音词条 |
| [07-consumables-misc.md](07-consumables-misc.md) | 神工、帖、食物、突破 |
