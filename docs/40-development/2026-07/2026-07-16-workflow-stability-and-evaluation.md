# Dev Log: 2026-07-16 工作流稳定性与评估体系闭环

> 日期：2026-07-16
> 涉及模块：`lvjiang/core/capture.py`、`lvjiang/core/input.py`、`lvjiang/core/material_recognizer.py`、`lvjiang/equip_parser/parser.py`、`lvjiang/workflows/engine.py`、`lvjiang/ui/run_control.py`、`lvjiang/__main__.py`、`config/system/equip_attrs.yaml`、`config/system/workflows/equip_analysis.wf`
> 关键词：海森堡 Bug、FAILSAFE、mss 跨线程 GDI、thread-local、ORB + Lab、装备评估、鸣金虹

---

## 一、本日完成

本日两个提交（`f3cacae`、`f4ff72f`），主线是**装备评估体系完整实现 + 工作流闪退根因定位**。

### 1.1 装备评估体系（`f3cacae`）

- **鸣金虹流派通用评估引擎**：转律熔断、优先级替换、重评级完整实现
- **统一扣分体系**：势 + 会意率扣分规则最终定型
- **装备解析器扩展**：支持从 `slot` 部位自动推断 `type`（ring→环、pendant→佩），不再依赖 OCR 文本中的类型字段
- **基础属性品阶推断**：`equip_attrs.yaml` 补充 ring/pendant/armor_other/chest 的 purple/blue 品阶数据

### 1.2 工作流稳定性修复（`f4ff72f`）

#### 1.2.1 材料识别升级

模板匹配 → **ORB 特征匹配 + CIE Lab 颜色距离** 双模态：
- 解决图标被减号遮挡后模板匹配失效问题
- 通过 Lab 色彩空间平均颜色距离区分形状相似但颜色不同的材料（如大律准石 vs 律准石）
- 加权公式：`score = 0.5 × ORB + 0.5 × Lab_color_sim`

#### 1.2.2 闪退根因定位（海森堡 Bug）

毕业率工作流在胫甲→腕甲切换时偶发"无日志、无弹窗"硬闪退。排查定位到**两个独立缺陷**：

| 缺陷 | 表现 | 触发条件 | 修复 |
|------|------|----------|------|
| pyautogui.FAILSAFE + 鼠标干扰 | 分神时抓鼠标甩向屏幕角落，`moveTo` 动画过程中触发 `FailSafeException`，异常被 QThread 吞掉 | 用户物理鼠标干扰 | `FAILSAFE = False`，停止由 `stop_check` 控制 |
| ScreenCapture 跨线程 GDI | mss 实例在主线程创建，`capture()` 在 QThread 调用，跨线程复用 GDI 设备上下文是未定义行为 | 窗口焦点切换 / DWM 重新合成 | `threading.local()` 惰性创建线程专属 mss 实例 |

**关键分析**：
- 崩溃位置（`moveTo`）与根因（上一步的 mss 跨线程）不一致——原生内存损坏常延迟到下一次原生调用才爆
- "一观察就正常、一分神就崩"是典型海森堡 Bug：观察行为（手不碰鼠标、窗口保持焦点）恰好消除了触发条件
- mss 跨线程是文档明确禁止的未定义行为，correct-by-construction，无需为复现去回退验证

#### 1.2.3 三层防御补全

- **日志防丢失**：loguru file handler 加 `enqueue=True` 异步写入，防止进程崩溃时缓冲丢失
- **逐槽容错**：DSL 引擎 `_execute_nodes` 容错扩展到所有 Step（原先仅非 click 步骤容错，click 异常会 raise 导致整个工作流崩溃）
- **结果展示与保存**：
  - 所有工作流结果通用保存至 `users/{用户名}/{工作流名}.json`
  - 完成后用 `EquipmentParser` 解析并逐件展示装备信息（部位 | 名称 | 类型 | 等级 | 词条 | 警告）

#### 1.2.4 工作流与配置修正

- `equip_analysis.wf`：ring/pendant 改用 `equip_weapon_detail` 场景（原错误使用 `equip_armor_detail`）
- `equip_attrs.yaml`：补充 ring/pendant/armor_other/chest 的 purple/blue 品阶数据

---

## 二、当前整体进度

对照 `docs/00-meta/roadmap.md`：

| Phase | 状态 | 说明 |
|-------|------|------|
| Phase 0 项目骨架 | ✅ 完成 | |
| Phase 1 坐标校准工具 | ✅ 完成 | 区域编辑器 + 画布中间层 + 吸附对齐 |
| Phase 2 POI 截取框架 + OCR 验证 | ✅ 完成 | OCR 测试对话框、材料识别模式 |
| Phase 3 UI 状态检测 | ⚠️ 部分 | 场景识别已实现，但无显式状态机 |
| Phase 4 词条解析器 | ✅ 完成 | EquipmentParser + 级联丢弃规则 |
| Phase 5 输入控制封装 | ✅ 完成 | InputController + 延迟配置化 |
| Phase 6 工作流编排 | ✅ 完成 | DSL 引擎 + .wf 文件 + 条件分支 + 内置函数 |
| Phase 7 规则引擎配置化 | ✅ 完成 | 流派 YAML + 扣分体系 + 转律熔断 |
| Phase 8 GUI 完善 | 🔄 进行中 | 日志面板、结果展示已实现；统计报表待做 |

**里程碑**：核心功能链路（OCR → 解析 → 评估 → 工作流 → 结果展示）**端到端打通**。

---

## 三、待完成事项

### 3.1 高优先级（影响主流程可用）

1. **毕业率工作流端到端真实验证**
   - 当前修复后连续跑通 03:33/03:36 两次，样本量不足
   - 需在真实游戏环境（含故意切窗口、乱动鼠标）下跑十几轮验证稳定性
   - 关注点：OCR 脏数据处理、装备数据自动保存到 `users/{用户名}/equipments.json` 的正确性

2. **鸣金虹流派评估引擎端到端验证**
   - 转律熔断、优先级替换、重评级逻辑已实现，但尚未在真实调律场景中验证
   - 需要配合 `单次调律测试` 工作流跑通完整调律循环

### 3.2 中优先级（提升鲁棒性）

3. **游戏无响应时的降级处理**
   - 当前页面不响应点击时，OCR 会读到旧页面数据，collect 会保存错误数据
   - 可考虑在 `scan` 后做最小校验（如 `equip_type` 不为空），失败时重试或跳过该槽位
   - 注意：不要过度设计，保持"失败静默容错"原则（见 memory `失败静默容错原则`）

4. **统计报表**
   - 处理装备数量、保留/回收/精调汇总
   - 调律次数分布、熔断触发次数
   - 可基于已保存的 `users/{用户名}/*.json` 离线分析

### 3.3 低优先级（体验优化）

5. **UI 状态显式状态机**
   - 当前场景识别是隐式的（每个 scan 指定场景 key）
   - 可考虑显式状态机：UNKNOWN → BAG → EQUIP_DETAIL → TUNE_PANEL → ...
   - 用于异常恢复（如检测到状态不对，自动回退到 BAG）

6. **多用户数据管理**
   - 当前 `users/` 目录按用户名隔离，但无切换/清理 UI
   - 可考虑在用户选择器旁增加"清空当前用户数据"按钮

7. **材料识别参考图精简**
   - 当前同一材料保留多个等级变体，实际只需一份（等级由 OCR 单独识别）
   - 可清理 `config/system/materials/` 下的冗余参考图

---

## 四、本日关键技术决策

### 4.1 mss 线程本地存储

```python
class ScreenCapture:
    def __init__(self):
        self._local = threading.local()
        self._monitor = None

    @property
    def _sct(self):
        sct = getattr(self._local, "sct", None)
        if sct is None:
            sct = mss.mss()
            self._local.sct = sct
        return sct
```

**理由**：mss 在 Windows 上缓存 GDI 设备上下文（srcdc/memdc），这些句柄有线程亲和性。跨线程调用 `BitBlt` 是未定义行为，窗口焦点切换时触发原生访问违例，Python 无法捕获。

### 4.2 材料识别双模态加权

```python
score = 0.5 * orb_score + 0.5 * lab_color_sim
```

**理由**：
- ORB 抗遮挡（图标被减号覆盖仍能匹配形状）
- Lab 区分颜色（大律准石 vs 律准石形状相同但颜色差异大）
- 加权组合兼顾两者优势

### 4.3 工作流结果通用保存

```python
def _save_workflow_result(self, name: str, result):
    username = self._user_manager.get_active_user_name()
    user_dir = LOCAL_CONFIG_DIR / "users" / username
    user_dir.mkdir(parents=True, exist_ok=True)
    save_path = user_dir / f"{name}.json"
    save_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
```

**理由**：所有工作流结果统一保存，后续可基于 JSON 做离线分析、统计报表、数据回溯。

---

## 五、文件变更汇总

| 文件 | 操作 | 说明 |
|------|------|------|
| `lvjiang/core/capture.py` | 修改 | mss 改用 thread-local，每线程独立实例 |
| `lvjiang/core/input.py` | 修改 | FAILSAFE=False + pyautogui 调用加 try/except |
| `lvjiang/core/material_recognizer.py` | 重写 | 模板匹配 → ORB + Lab 双模态 |
| `lvjiang/equip_parser/parser.py` | 修改 | 支持从 slot 推断 type |
| `lvjiang/workflows/engine.py` | 修改 | 逐槽容错扩展到所有 Step |
| `lvjiang/ui/run_control.py` | 修改 | 通用保存 + 结果展示 |
| `lvjiang/__main__.py` | 修改 | loguru enqueue=True |
| `config/system/equip_attrs.yaml` | 修改 | 补充 purple/blue 品阶数据 |
| `config/system/workflows/equip_analysis.wf` | 修改 | ring/pendant 改用 equip_weapon_detail |

---

## 六、下一步行动建议

1. **优先**：在真实环境跑十几轮毕业率工作流，验证稳定性
2. **次优先**：跑通单次调律测试工作流，验证鸣金虹评估引擎
3. **可选**：基于已保存的 JSON 数据做统计报表原型
