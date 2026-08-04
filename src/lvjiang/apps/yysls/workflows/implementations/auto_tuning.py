"""自动调律工作流 — 端到端流水线（编排层）

状态机三行为点（tuning_base.behavior + materials）驱动流程决策，
流派规则仅作装备预期识别（输入装备 → 输出预期评级上限）：
- 扫描处理（behavior.scan）：进调律前，传入规则预期 ≥ 进入门槛
  即进调律；未达门槛按处置表决定回收/保留（_on_scan_reject）；
- 材料处理（materials）：每轮调律开始前的律准石检查与狗粮决策；
- 结束处理（behavior.tune）：每轮调律结束后按预期评级决策
  继续调律/重置调律/回收/结束保留，词条满为边界条件（continue
  不可选，无命中默认结束保留）；背包读到已满装备走扫描处理
  同一路径（tune_full_recycle 视为直接回收）。
回收后背包补位，由 _process_equipment 就地重读同格续处理。

调律功能按职责拆分为 tuning 包下四个独立类（组合引用）：
- TuningJudge: 判定与评级（纯逻辑）
- TuningExecutor: 调律执行（材料检查、狗粮决策、单轮调律）
- TuningNavigator: 导航（页面跳转、词条收集）
- TuningRecycler: 重置与回收

背包滚动遍历抽象为策略类（bag_traversal：dedup 滑动窗口去重 /
positional 位置对齐三向校验），_traverse_bag 按配置调度，默认 dedup。
"""

from loguru import logger

from lvjiang.apps.yysls.equip_parser import EquipmentData
from lvjiang.apps.yysls.evaluator import (
    get_rule_names,
    judge_equipment_potential,
    summarize_potential,
)
from lvjiang.apps.yysls.evaluator.tuning_rules import (
    RATING_LABELS,
    RATING_RANK,
    get_tuning_base,
)
from lvjiang.apps.yysls.workflows.implementations.bag_traversal import (
    DEFAULT_TRAVERSAL,
    TRAVERSALS,
)
from lvjiang.apps.yysls.workflows.implementations.tuning import (
    TuningExecutor,
    TuningJudge,
    TuningNavigator,
    TuningRecycler,
)
from lvjiang.apps.yysls.workflows.run_context import TuningContextMixin
from lvjiang.apps.yysls.workflows.tuning_doc import TuningDocWriter
from lvjiang.workflows.base import BaseWorkflow


class AutoTuningWorkflow(TuningContextMixin, BaseWorkflow):
    """自动调律工作流"""

    # 脚本元数据（供发现层暴露到日常下拉与设备端悬浮面板）。
    # 暂不定义 PARAMETERS：本流程的配置面（部位多选 + 每规则玩法多选 + 全局开关）
    # 超出现有 select/number 参数 schema 的表达力，设备端先按内置默认配置运行
    # （全部部位 + 全部规则默认判定，见 _ensure_judge_config 的回退），
    # 细粒度配置仍只在桌面调律 Tab 经 run_ctx 注入。
    DISPLAY_NAME = "自动调律"

    # ─── 共享常量 ──────────────────────────────────────────

    WEAPON_SLOTS = ["main_weapon", "sub_weapon", "ring", "pendant"]
    ARMOR_SLOTS = ["head", "chest", "leg", "wrist"]
    WEAPON_DETAIL = "equip_weapon_detail"
    ARMOR_DETAIL = "equip_armor_detail"
    EQUIP_DETAIL = "equip_detail"  # 通用交互区域（more_func/sub_func_*/recycle_*）

    SCAN_FIELDS = [
        "equip_type", "equip_level", "base_attr",
        "affix_gong", "affix_shang", "affix_jue",
        "affix_zhi", "affix_yu",
    ]

    GRID_SCENE = "bag_equip_detail"
    GRID_PANEL = "bag_grid"

    TUNE_SCENE = "equip_tune_detail"
    # 调律结果弹窗已并入调律页（result 视图），运行时同场景寻址
    RESULT_SCENE = "equip_tune_detail"
    MATERIAL_PANEL = "materials"  # 材料区 panel key（7 列 1 行 grid）
    MATERIAL_COLS = 7  # 材料格列数
    MATERIAL_GROUP = "调律材料"
    MAX_AFFIX = 5

    # ─── 编排层状态 ────────────────────────────────────────

    # 调律说明文档（叙事输出，与技术日志分离）：run() 内创建，
    # 创建失败/未走 run() 时保持 None，各插桩点均先判空
    _doc: TuningDocWriter | None = None
    _doc_seq = 0                # 说明文档中的装备序号
    _equipment_recycled = False  # 最近一次 _process_equipment 是否触发了回收
    _tune_full_recycle_mode = False  # 调满后回收模式（跳过狗粮与规则判定，调满后回收）
    _expect_rating: str | None = None  # 当前装备期望评级 key（适用规则最高档）

    # ─── 功能组件（懒初始化）──────────────────────────────

    _judge: TuningJudge | None = None
    _executor: TuningExecutor | None = None
    _navigator: TuningNavigator | None = None
    _recycler: TuningRecycler | None = None

    @property
    def judge(self) -> TuningJudge:
        if self._judge is None:
            self._judge = TuningJudge(self.ctx)
        return self._judge

    @property
    def executor(self) -> TuningExecutor:
        if self._executor is None:
            self._executor = TuningExecutor(self)
        return self._executor

    @property
    def navigator(self) -> TuningNavigator:
        if self._navigator is None:
            self._navigator = TuningNavigator(self)
        return self._navigator

    @property
    def recycler(self) -> TuningRecycler:
        if self._recycler is None:
            self._recycler = TuningRecycler(self)
        return self._recycler

    # ─── 生命周期 ─────────────────────────────────────────

    @property
    def is_stopped(self) -> bool:
        """停止请求或材料耗尽（大律准石低于基准）都视为停止，
        背包遍历与部位循环全部收束，复用 F10 中断的退出路径"""
        return self.executor.materials_exhausted or super().is_stopped

    @staticmethod
    def _missing_output_fields() -> list[str]:
        """当前图库空间缺失的必需输出字段 key（调律启动预检）"""
        from lvjiang.apps.yysls.core.material_recognizer import (
            get_missing_output_fields,
        )
        from lvjiang.core.reference_db import ReferenceDatabase
        db = ReferenceDatabase()
        db.load()
        return get_missing_output_fields(db)

    def run(self) -> dict:
        # 预检：当前图库空间必须满足调律输出字段契约（levels/counts）
        missing = self._missing_output_fields()
        if missing:
            msg = (f"当前图库空间缺少输出字段 {'、'.join(missing)}，"
                   f"无法启动自动调律（请在图库元数据定义中补充）")
            logger.error(msg)
            return {"error": msg}

        self.executor.reset_state()
        self._ensure_judge_config()
        # 加载导航所需的 DSL subcall 文件（每次运行都重新加载，保证修改立即生效）
        self.navigator.load_dependencies()

        selected = self._resolve_selected_slots()

        self._open_doc(selected)
        try:
            self.navigator.navigate_to_equip()

            first_slot = True
            for slot in selected:
                if self.is_stopped:
                    break
                detail = (self.WEAPON_DETAIL if slot in self.WEAPON_SLOTS
                          else self.ARMOR_DETAIL if slot in self.ARMOR_SLOTS
                          else None)
                if detail is None:
                    continue

                if first_slot and self.ctx.target_cell:
                    # 指定调律：只处理这一件即收工
                    self._process_slot_group_enter(slot)
                    r, c = self.ctx.target_cell
                    self._process_single_target(detail, r, c)
                    break

                if first_slot and self.ctx.skip_start:
                    # 初始跳过：先滚到指定行，再正常遍历
                    self._process_slot_group_enter(slot)
                    self._scroll_to_row(self.ctx.skip_start[0])
                    self._traverse_bag(detail)
                else:
                    self._process_slot_group(slot, detail)

                first_slot = False

            if self.is_stopped:
                logger.info("自动调律被中断（F10/材料耗尽），保留当前页面")
                if "stop_reason" not in self.output:
                    self.output["stop_reason"] = "用户中断（F10）"
            else:
                self.navigator.navigate_back()
                logger.info("自动调律完成")
        finally:
            self._close_doc()
        return self.output

    # ─── 调律说明文档（可交付阅读的叙事输出）──────────────

    def _open_doc(self, slots: list[str]):
        """创建本次运行的说明文档并写文档头；失败只警告不中断流程。

        操作用户名由 run_control 注入 ctx.doc_username；输出目录可用
        ctx.doc_dir 覆盖（供测试），缺省 logs/tuning/。
        """
        username = self.ctx.doc_username or "default"
        try:
            self._doc = TuningDocWriter(username, self.ctx.doc_dir)
        except OSError as e:
            logger.warning(f"调律说明文档创建失败，本次不生成说明: {e}")
            self._doc = None
            return
        self._doc_seq = 0
        # 各规则配置的开关聚合（any 语义），再映射为显示名 → 状态
        merged: dict[str, bool] = {}
        for cfg in (self.ctx.judge_configs or {}).values():
            for k, v in (cfg.get("switches") or {}).items():
                merged[str(k)] = merged.get(str(k), False) or bool(v)
        try:
            names = get_tuning_base().switches
        except Exception:
            names = {}
        switches = {names.get(k, k): v for k, v in merged.items()}
        self._doc.start_run(username, self._describe_rules(), slots, switches)
        logger.info(f"调律说明文档: {self._doc.path}")

    def _describe_rules(self) -> list[str]:
        """由判定配置生成规则描述：显示名（玩法：勾选项）"""
        configs = self.ctx.judge_configs or {}
        if not configs:
            return []
        names = get_rule_names()
        descs = []
        for key, cfg in configs.items():
            name = names.get(key, key)
            playstyles = cfg.get("playstyles")
            if playstyles:
                name += f"（玩法：{'、'.join(playstyles)}）"
            descs.append(name)
        return descs

    def _close_doc(self):
        """写运行小结（含 F10 中断标记）+ 成品清单并关闭文档"""
        if self._doc is None:
            return
        reports = self.output.get("tuning_reports") or []
        tuned = [r for r in reports if r.get("status") == "tuned"]
        total_rounds = sum(int(r.get("rounds") or 0) for r in tuned)
        self._doc.end_run(self.is_stopped, len(tuned), total_rounds)
        self._doc.run_summary(self._summary_items(tuned))
        self._doc.close()
        self._doc = None

    @staticmethod
    def _summary_items(tuned: list[dict]) -> list[dict]:
        """从已调律 report 筛选最终评级一般及以上的成品清单项

        取各适用规则（非跳过/非不适用）的最高评级为准；
        rating_text 罗列各适用规则的评级结论。已回收的装备不入清单。
        """
        label_to_key = {v: k for k, v in RATING_LABELS.items()}
        items: list[dict] = []
        for r in tuned:
            if r.get("recycled"):
                continue
            ratings: list[str] = []
            best: str | None = None
            for j in (r.get("final_judgement") or {}).values():
                if j.get("skipped") or j.get("not_applicable"):
                    continue
                rating = j.get("rating", "")
                ratings.append(f"{j.get('name', '')}：{rating}")
                key = label_to_key.get(rating)
                if key and (best is None
                            or RATING_RANK[key] > RATING_RANK[best]):
                    best = key
            if best is None or RATING_RANK[best] < RATING_RANK["normal"]:
                continue
            items.append({
                "name": r.get("name"),
                "type": r.get("type"),
                "level": r.get("level"),
                "quality": r.get("quality"),
                "rating_text": "；".join(ratings),
                "affixes": r.get("final_affixes") or [],
            })
        return items

    # ─── 部位处理 ──────────────────────────────────────────

    def _process_slot_group_enter(self, slot: str):
        """点击部位标签进入背包网格页"""
        logger.info(f"── 处理部位: {slot} ──")
        self.click_region(self.GRID_SCENE, slot)
        self.wait_delay("page_refresh_wait")  # 背包浏览页 → 装备详情页

    def _process_slot_group(self, slot: str, detail_scene: str):
        self._process_slot_group_enter(slot)
        self._traverse_bag(detail_scene)

    def _scroll_to_row(self, target_row: int) -> None:
        """滚动背包网格使 target_row 出现在可见区第一行

        每次 drag_grid("up") 步进一行。target_row <= 1 时无操作。
        """
        if target_row <= 1:
            return
        drags = target_row - 1
        logger.info(f"滚动定位: 目标行={target_row}, 需拖拽{drags}次")
        for _i in range(drags):
            if self.is_stopped:
                break
            self.drag_grid(self.GRID_SCENE, self.GRID_PANEL, "up", hold=0.3)
            self.wait_delay("scroll_settle_wait")
        self.align_panel(self.GRID_SCENE, self.GRID_PANEL)

    def _process_single_target(self, detail_scene: str, row: int, col: int) -> None:
        """指定调律：定位到 (row, col)，只处理这一件，含完整处理链"""
        self._scroll_to_row(row)
        if self.is_stopped:
            return
        # 滚动后目标行在可见区第 1 行
        name, fp, equip = self._read_row(detail_scene, 1, col)
        if not fp:
            logger.warning(f"指定调律: 目标格 ({row},{col}) 为空，无装备可处理")
            return
        logger.info(f"指定调律: 定位到 ({row},{col})，装备={name or fp}")
        self._process_equipment(name, equip, detail_scene, row=1, col=col)

    # ─── 背包遍历（滚动策略实现见 bag_traversal）──────────────

    def _read_row(self, detail_scene: str, row: int,
                  col: int = 1) -> tuple[str, str, dict]:
        """点击指定行/列 slot，等面板刷新后 OCR，返回 (装备名, 指纹, 装备dict)

        默认第 1 列（行指纹/滚动校验链路）；列遍历时传入 col 读非首列。"""
        self.click_panel(self.GRID_SCENE, self.GRID_PANEL, row, col)
        self.wait_delay("page_refresh_wait")
        fields = self.SCAN_FIELDS + (["base_attr_2"]
                                     if detail_scene == self.ARMOR_DETAIL else [])
        raw = self.ocr_scene(detail_scene, fields)
        equip = self.call_function("to_equipment", [raw]) if raw else {}
        name = (equip.get("name") or "") if equip else ""
        fp = self._make_fingerprint(equip)
        if fp:
            logger.debug(
                f"  读格 grid[{row}][{col}] → {self._equip_label(equip)} "
                f"lv={equip.get('level')} quality={equip.get('quality')} fp={fp}")
        else:
            logger.debug(f"  读格 grid[{row}][{col}] → 空")
        return name, fp, equip

    def _process_row_cols(self, detail_scene: str, win_row: int,
                          logical_row: int, cols: int):
        """遍历该行第 2..cols 列装备（第 1 列由行指纹链路处理）。

        复用首列链路：click_panel grid[win_row][col] → OCR → _process_equipment。
        非首列装备不参与滚动校验，无需记录指纹（锚点仍只用首列），
        调律后指纹变化也无需回写。背包按行优先填充，空 slot 表示
        该行装备到此为止，停止本行遍历。
        回收后后续装备前移补位到当前列，需留在当前列继续处理，
        而非跳到下一列（否则漏件）。
        """
        col = 2
        while col <= cols:
            if self.is_stopped:
                break
            name, fp, equip = self._read_row(detail_scene, win_row, col)
            if not fp:
                logger.info(
                    f"  行{logical_row} grid[{win_row}][{col}] 空 slot → 本行到此为止")
                break
            logger.info(f"  行{logical_row} grid[{win_row}][{col}] {name} fp={fp}")
            self._process_equipment(name, equip, detail_scene,
                                    row=win_row, col=col)
            if self._equipment_recycled:
                # 回收使后续装备前移补位到当前列，留在当前列继续处理
                if not fp:
                    break   # 安全兜底：回收后同列读空 → 本行结束
                continue
            col += 1

    def _traverse_bag(self, detail_scene: str):
        """按配置选择遍历策略并执行（策略实现见 bag_traversal）

        优先级：注入配置 ctx.scroll_strategy > 插件 session tuning 节的
        scroll_strategy > 默认 DEFAULT_TRAVERSAL。回切旧方案：在
        config/session/yysls/session.json 的 tuning 节配
        "scroll_strategy": "positional"。
        """
        key = self.ctx.scroll_strategy or ""
        if not key:
            try:
                from lvjiang.apps.yysls.plugin_session import get_plugin_session
                key = (get_plugin_session().get_section("tuning")
                       .get("scroll_strategy") or "")
            except Exception as e:  # noqa: BLE001
                logger.warning(f"读取遍历策略配置失败，用默认: {e}")
        if key and key not in TRAVERSALS:
            logger.warning(f"未知遍历策略 {key!r}，回落默认 {DEFAULT_TRAVERSAL}")
        if key not in TRAVERSALS:
            key = DEFAULT_TRAVERSAL
        logger.info(f"背包遍历策略: {key}")
        TRAVERSALS[key]().traverse(self, detail_scene)

    # ─── 单件装备处理（潜力判定 + 实际调律 + 回报）────────

    def _process_equipment(self, name: str, equip: dict, detail_scene: str,
                           row: int | None = None, col: int = 1) -> str:
        """单件处理入口：回收后就地重读同格，续处理补位装备。

        row/col 为该件在背包窗口中的格位；回收使后续装备前移补位，
        有格位时重读同格续处理（保险丝防死循环）；row=None 表示调用方
        无格位信息，回收后无法回读，返回空指纹由上层按空 slot 处理。
        返回最终占位装备的（终态）指纹，供上层更新行指纹。
        设置 _equipment_recycled 标记供 _process_row_cols 判断是否
        需要在同一列继续处理（补位导致后续装备前移）。
        """
        self._equipment_recycled = False
        fp, recycled = self._process_equipment_once(name, equip, detail_scene)
        if not recycled:
            return fp
        if row is None:
            self._equipment_recycled = True
            return ""
        for _ in range(200):
            if self.is_stopped:
                break
            name, fp, equip = self._read_row(detail_scene, row, col)
            if not fp:
                logger.info(f"  grid[{row}][{col}] 回收后已空，该格结束")
                self._equipment_recycled = True
                return ""
            logger.info(f"  回收补位 grid[{row}][{col}] → {name} fp={fp}")
            fp, recycled = self._process_equipment_once(name, equip,
                                                        detail_scene)
            if not recycled:
                self._equipment_recycled = True
                return fp
        self._equipment_recycled = True
        return ""

    def _process_equipment_once(self, name: str, equip: dict,
                                detail_scene: str) -> tuple[str, bool]:
        """对一件装备走完整链路：潜力判定 → 值得则调律到满 → 终局判定 → 回报。

        进入时已停在 bag_equip_detail（含 detail 区，_read_row 已点选该件）。
        未调律分支（already_full/junk_blank/no_tune_entry）保持在背包页；
        已调律分支调完后单次 back 返回背包浏览页，供遍历继续滚动。
        skip_tuning 测试开关只拦截「值得调律且已进调律页」的装备：
        词条已满/不值得/无入口仍走各自正常分支，不会统统进调律页。
        仅真正进过调律页的装备（tuned/skip_tuning）与异常的 no_tune_entry
        产出 report 追加进 output["tuning_reports"]；词条已满/垃圾胚子未做
        调律，不收集（一次遍历几百件时会淹没有效信息）。
        返回 (调律后终态指纹, 是否已回收)；已回收时指纹为空串
        （该格已被补位装备占据，由 _process_equipment 重读）。
        """
        if not equip:
            return "", False

        # 前置拦截 0：等级门槛 — 低于 min_level 直接跳过，不走任何判定
        level = equip.get("level") or 0
        min_level = get_tuning_base().min_level
        if level < min_level:
            logger.info(
                f"  [{name}] 等级 {level} < 门槛 {min_level}，直接跳过")
            return self._make_fingerprint(equip), False

        # 前置拦截 1：品阶识别异常 — 无法识别品阶说明 OCR 有问题，直接跳过
        quality = equip.get("quality")
        if not quality:
            logger.warning(
                f"  [{name}] 品阶识别失败（quality 为空），视为异常直接跳过")
            return self._make_fingerprint(equip), False

        equip_data = EquipmentData.from_dict(equip)
        affix_count = int((equip.get("_extra") or {}).get("affix_count", 0))
        logger.info(
            f"  处理装备: {self._equip_label(equip)} "
            f"quality={equip.get('quality')} affix_count={affix_count}")
        report: dict = {
            "name": name,
            "type": equip.get("type"),
            "level": equip.get("level"),
            "quality": equip.get("quality"),
            "affix_count": affix_count,
            "resets": 0,
        }

        # A. 词条已满 → 终局判定 + 扫描处理（已满不进调律，
        #    走 scan 行为点决定回收/保留）；未处理过，不收集 report
        if affix_count >= self.MAX_AFFIX:
            logger.info(f"  [{name}] 词条已满（{affix_count}），仅做终局判定")
            judgement = self.judge.final_judge(equip_data)
            report["status"] = "already_full"
            report["final_judgement"] = judgement
            if self._on_scan_reject(equip_data, judgement, detail_scene,
                                    already_full=True):
                return "", True
            return self._make_fingerprint(equip_data.to_dict()), False

        # B. 进入决策（scan 行为点）：传入规则预期评级 ≥ 进入门槛
        #    → 进调律；未达门槛按扫描处置表决定回收/保留。
        #    未处理过，不收集 report，也不写说明文档
        scan_cfg = get_tuning_base().behavior.scan
        potential = judge_equipment_potential(
            equip_data, self.ctx.judge_configs, self.ctx.judge_rule_keys)
        _, logs = summarize_potential(potential)
        expect = self.judge.expect_key(potential)
        self._expect_rating = expect
        for line in logs:
            logger.info(f"  潜力判定 | {line}")
        report["worthiness"] = logs
        # 重置调满后回收模式标记（每件装备重新判断）
        self._tune_full_recycle_mode = False
        if (expect is None or RATING_RANK[expect]
                < RATING_RANK[scan_cfg.entry_min_rating]):
            entry_label = RATING_LABELS.get(scan_cfg.entry_min_rating,
                                            scan_cfg.entry_min_rating)
            logger.info(f"  [{name}] 预期评级未达进入门槛"
                        f"（≥{entry_label}），不进调律")
            if self._on_scan_reject(equip_data, potential, detail_scene):
                return "", True
            # 调满后回收模式：继续走调律流程
            if self._tune_full_recycle_mode:
                logger.info(f"  [{name}] 进入调满后回收模式")
            else:
                return self._make_fingerprint(equip_data.to_dict()), False

        # C. 值得 → 实际调律；说明文档开装备节（只写命中的规则）
        if self._doc:
            self._doc_seq += 1
            self._doc.start_equipment(self._doc_seq, equip)
            self._doc.worthiness_matched(potential)
        if not self.navigator.nav_to_tune():
            logger.info(f"  [{name}] 未找到调律入口，跳过")
            report["status"] = "no_tune_entry"
            if self._doc:
                self._doc.note("已符合规则但未找到调律入口，跳过本件")
            self.output.setdefault("tuning_reports", []).append(report)
            return self._make_fingerprint(equip_data.to_dict()), False

        # 临时测试开关：只有词条未满、判定值得调律且找到入口的装备
        # 才走到这里；真实进出调律页但不执行调律，供滚动遍历压测用。
        # 装备未被改动，不走垃圾/完成后处理挂载点。
        if self.ctx.skip_tuning:
            logger.info(f"  [{name}] 值得调律，但跳过实际调律（测试开关）")
            if self._doc:
                self._doc.note("值得调律，但测试开关已启用，跳过实际调律")
            self.click_region(self.TUNE_SCENE, "back")
            self.wait_delay("page_refresh_wait")  # 调律页 → 背包详情页
            self.click_region(self.EQUIP_DETAIL, "more_func")
            self.wait_delay("step_interval")
            report["status"] = "skip_tuning"
            self.output.setdefault("tuning_reports", []).append(report)
            return self._make_fingerprint(equip_data.to_dict()), False

        # 结束处理支撑：首词条快照（重置后仅剩首词条）+ 本件重置计数
        tune_cfg = get_tuning_base().behavior.tune
        base_affixes = list(equip_data.affixes)
        resets_used = 0
        tune_recycle_reason = ""

        rounds = 0
        stop_reason = ""
        last_food_reason = ""
        while not self.is_stopped:
            logger.info(f"  ═══ [{name}] 调律轮次 #{rounds + 1}"
                        f"（当前 {affix_count}/{self.MAX_AFFIX}）═══")
            self.wait_delay("page_refresh_wait")
            result = self.executor.tune_once(
                equip_data, self._expect_rating, self._tune_full_recycle_mode)
            if result is None:
                stop_reason = self.executor.abort_reason or "无法继续调律"
                report["stop_reason"] = stop_reason
                if self._doc:
                    self._doc.note(f"{stop_reason}，结束调律")
                break
            rounds += 1
            affix_count += 1
            # 每轮结果挂在本件 report 下，与装备一一对应
            report.setdefault("tune_results", []).append(result)
            new_affix = self.navigator.collect_new_affix(
                equip_data, result.get("tune_affix", "")) \
                or result.get("tune_affix", "")
            if self._doc:
                if self.executor.round_food_reason != last_food_reason:
                    self._doc.food_strategy(self.executor.round_food_reason)
                    last_food_reason = self.executor.round_food_reason
                self._doc.tune_round(rounds, self.executor.round_food,
                                     new_affix)
            # 结束处理（tune 行为点）：传入规则口径刷新预期评级
            # （供狗粮决策与说明文档）→ 行为表决策，评级按各规则
            # 自身判定语义懒取；词条满为边界条件（continue 不可达）
            full = affix_count >= self.MAX_AFFIX
            # 调满后回收模式：跳过规则判定，调满即回收
            if self._tune_full_recycle_mode:
                if full:
                    why = "调满后回收模式：词条已满，执行回收"
                    logger.info(f"  [结束处理] {why}")
                    tune_recycle_reason = why
                    stop_reason = why
                    report["stop_reason"] = stop_reason
                    if self._doc:
                        self._doc.round_decision(why)
                    break
                else:
                    why = f"调满后回收模式：继续调律（{affix_count}/{self.MAX_AFFIX}）"
                    logger.info(f"  [结束处理] {why}")
                    if self._doc:
                        self._doc.round_decision(why)
                    continue
            incoming, self._expect_rating = self.judge.refresh_expectation(
                equip_data)
            part, quality, cap_pct = self.judge.recycle_inputs(equip_data)
            rating_of = self.judge.rating_provider(equip_data, incoming)
            action, why = tune_cfg.decide(part, quality, cap_pct, rating_of,
                                          full,
                                          [a.name
                                           for a in equip_data.affixes])
            logger.info(f"  [结束处理] {why}")
            if action == "continue":
                if self._doc:
                    self._doc.round_decision(why)
                continue
            if action == "reset":
                result = self.recycler.try_reset_tune(
                    tune_cfg, resets_used, why)
                if result is True:
                    resets_used += 1
                    report["resets"] = resets_used
                    # 重置清空首词条以外全部词条 → 只剩首词条
                    equip_data.affixes = base_affixes[:1]
                    equip_data.extra_data["affix_count"] = 1
                    affix_count = 1
                    if self._doc:
                        self._doc.note(
                            f"重置调律（第 {resets_used} 次）：{why}")
                    continue
                if isinstance(result, str):
                    # 冷却期拒绝 → 强制跳过（不走 reset_exhausted_action）
                    action = "skip"
                    why = result
                else:
                    # 重置次数已用尽（本地上限或按钮无次数）→ 按配置转处置
                    action = tune_cfg.reset_exhausted_action
                    if action == "recycle":
                        why = f"重置次数已用尽转回收（{why}）"
                        logger.info(f"  [结束处理] {why}")
            # 回收延到 back 回背包页后执行；skip = 结束保留
            if action == "recycle":
                tune_recycle_reason = why
            stop_reason = why
            report["stop_reason"] = stop_reason
            if self._doc:
                self._doc.round_decision(why)
            break

        # 返回背包浏览页：调律页单次 back 即回到 bag_equip_detail（装备位置保持不变）。
        # 因进调律前点过「更多」弹出子菜单，back 回来后弹窗仍在，
        # 再点一次「更多」more_func 使其收起，保持背包页干净以继续遍历。
        self.click_region(self.TUNE_SCENE, "back")
        self.wait_delay("page_refresh_wait")  # 调律页 → 背包详情页（页面切换）
        self.click_region(self.EQUIP_DETAIL, "more_func")
        self.wait_delay("step_interval")

        judgement = self.judge.final_judge(equip_data)
        report["status"] = "tuned"
        report["rounds"] = rounds
        report["final_affix_count"] = affix_count
        report["final_judgement"] = judgement
        # 终态词条（含逐轮新增），供运行结束时的成品清单
        report["final_affixes"] = [a.to_dict() for a in equip_data.affixes]
        logger.info(f"  [{name}] 调律结束：共 {rounds} 轮，词条 {affix_count}/{self.MAX_AFFIX}")
        if self._doc:
            if not stop_reason:
                stop_reason = ("用户中断（F10）" if self.is_stopped
                               else "调律结束")
            self._doc.finish_equipment(rounds, affix_count, stop_reason,
                                       judgement)
        # 命中回收的装备在回到背包页后执行（阻断时不回收）
        recycled = False
        if tune_recycle_reason and not self.is_stopped:
            recycled = self.recycler.recycle_current(
                equip_data, detail_scene, "tune", tune_recycle_reason,
                report)
        self.output.setdefault("tuning_reports", []).append(report)
        if recycled:
            return "", True
        return self._make_fingerprint(equip_data.to_dict()), False

    # ─── 行为处置（按 tuning_base.behavior 行为点配置驱动）──────

    def _on_scan_reject(self, equip_data: EquipmentData, potential: dict,
                        detail_scene: str | None = None,
                        already_full: bool = False) -> bool:
        """扫描阶段处置（scan 行为点）。

        两种入口：
        1. 未满装备预期评级未达进入门槛 → 按处置表决定回收/保留；
        2. 已满装备（already_full=True）→ 同走处置表，其中
           tune_full_recycle 视为直接回收（已满无需再调）。

        处置表评级按各规则自身判定语义懒取（incoming 复用进入
        决策的判定结果，all/custom 按需另跑潜力判定并缓存；
        逐规则声明的 first_affix_only 由评级提供者注入首词条）→
        处置表首条命中；仅 recycle 动作落地，tune_full_recycle
        设置标记后返回 False（继续走调律流程），其余保留。

        Returns: True=已回收（该格已被补位装备占据）。
        """
        label = equip_data.name or equip_data.type
        cfg = get_tuning_base().behavior.scan
        if detail_scene is None or not cfg.enabled:
            logger.info(f"  [扫描处理] {label} 不进调律（处置未启用，保留）")
            return False
        part, quality, cap_pct = self.judge.recycle_inputs(equip_data)
        action, why = cfg.decide(part, quality, cap_pct,
                                 self.judge.rating_provider(equip_data,
                                                            potential),
                                 [a.name for a in equip_data.affixes])
        if action == "tune_full_recycle":
            if already_full:
                # 已满装备无需再调，"调满后回收"意图等价于直接回收
                logger.info(f"  [扫描处理] {label} {why}（已满，直接回收）")
                return self.recycler.recycle_current(equip_data, detail_scene,
                                                     "scan", why)
            # 未满：设置标记，继续走调律流程
            logger.info(f"  [扫描处理] {label} {why}（调满后回收模式）")
            self._tune_full_recycle_mode = True
            return False
        if action != "recycle":
            logger.info(f"  [扫描处理] {label} {why}")
            return False
        return self.recycler.recycle_current(equip_data, detail_scene,
                                             "scan", why)

    # ─── 工具方法（bag_traversal 经 wf 调用）──────────────

    @staticmethod
    def _make_fingerprint(equip: dict) -> str:
        """生成装备指纹，空装备返回空串。

        空装备判定：type/name/level 全为空或 None。
        """
        if not isinstance(equip, dict) or not equip:
            return ""
        # 检查是否有有效装备数据（type/level 至少有一个非空）
        equip_type = equip.get("type") or ""
        equip_name = equip.get("name") or ""
        equip_level = equip.get("level") or ""
        if not equip_type and not equip_name and not equip_level:
            return ""  # 空装备 → 空指纹
        import hashlib
        parts = [
            str(equip_type),
            str(equip_level),
            str(equip.get("quality", "") or ""),
            str(equip.get("chengyin", "") or ""),
        ]
        for i in range(1, 6):
            affix = equip.get(f"affix_{i}")
            if isinstance(affix, dict) and affix.get("name"):
                parts.append(f"{affix['name']}:{affix.get('value', '')}")
        raw = "+".join(parts)
        return hashlib.md5(raw.encode()).hexdigest()[:8]

    @staticmethod
    def _equip_label(equip: dict) -> str:
        """日志用装备标识：name|type（缺失部分省略，全空返回'空'）"""
        if not isinstance(equip, dict) or not equip:
            return "空"
        label = "|".join(
            str(p) for p in (equip.get("name"), equip.get("type")) if p)
        return label or "空"

    # ─── 判定配置兜底 ──────────────────────────────────────

    def _ensure_judge_config(self):
        """保证 ctx.judge_configs/judge_rule_keys 可用。

        优先用 run_control 注入的实时 UI 配置；未注入时回退读插件会话
        tuning.rules + tuning.switches，
        无有效配置时回退 (None, None) 即全部规则默认配置。
        """
        ctx = self.ctx
        if ctx.judge_configs is not None or ctx.judge_rule_keys is not None:
            return
        try:
            from lvjiang.apps.yysls.plugin_session import get_plugin_session
            section = get_plugin_session().get_section("tuning")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"读取调律规则配置失败，按全部规则默认判定: {e}")
            return
        raw = section.get("rules")
        if not isinstance(raw, dict):
            return
        switches = {str(k): bool(v)
                    for k, v in (section.get("switches") or {}).items()}
        enabled = {k: {**cfg, "switches": switches}
                   for k, cfg in raw.items()
                   if isinstance(cfg, dict) and cfg.get("enabled")}
        if enabled:
            ctx.judge_rule_keys = list(enabled)
            ctx.judge_configs = enabled

    def _resolve_selected_slots(self) -> list[str]:
        """调律部位：优先 UI 注入的 ctx.selected_slots；设备端经 task_runner
        启动时 ctx 未注入（selected_slots=None），回退读插件会话
        tuning.selected_slots（与 _ensure_judge_config 读 rules/switches 对称）；
        仍无有效配置时按全部部位。"""
        selected = self.ctx.selected_slots
        if selected is None:
            try:
                from lvjiang.apps.yysls.plugin_session import get_plugin_session
                raw = (get_plugin_session().get_section("tuning")
                       .get("selected_slots"))
            except Exception as e:  # noqa: BLE001
                logger.warning(f"读取调律部位配置失败，按全部部位: {e}")
                raw = None
            if isinstance(raw, list):
                valid = set(self.WEAPON_SLOTS) | set(self.ARMOR_SLOTS)
                selected = [s for s in raw if s in valid]
        if not selected:
            selected = self.WEAPON_SLOTS + self.ARMOR_SLOTS
        return selected
