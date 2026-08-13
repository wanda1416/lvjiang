"""数据指令 Mixin：scan / recognize / collect / eval / call proc"""

from typing import Any

from loguru import logger

from ..grammar import (
    ArithOp,
    ByClause,
    CallProc,
    Collect,
    Eval,
    EvalFieldChainAssign,
    FieldAccess,
    Find,
    FuncCall,
    KeywordRef,
    Literal,
    PanelRef,
    Recognize,
    Scan,
    SceneRef,
    VarRef,
)
from .signals import WorkflowUserError, _ReturnSignal


class _DataOpsMixin:
    """数据指令执行：OCR/识别取数、collect 输出、eval 赋值、过程调用"""

    def _resolve_literal(self, value) -> Any:
        """递归解析 literal 内的 VarRef/FieldAccess（用于 default 的 dict/list）

        dict/list 的 value 可能包含 VarRef 或 FieldAccess，需要递归解析。
        其他类型（str/int/float/bool/None）直接返回。
        """
        if isinstance(value, dict):
            return {k: self._resolve_literal(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self._resolve_literal(item) for item in value]
        if isinstance(value, (VarRef, FieldAccess)):
            return self._resolve(value)
        return value

    def _dynamic_field_keys(self, region_var) -> list[str]:
        """[scene].$var 动态区域 → 字段 key 列表

        变量未定义/为空时 str(None) 会变成 "None" 这种永远查不到的假 key，
        识别层只能报「区域未绑定坐标」，看不出根因是变量没值，故先拦一道。
        """
        region_key = self._resolve(region_var)
        if region_key is None or region_key == "" or region_key == []:
            desc = getattr(region_var, "name", region_var)
            raise WorkflowUserError(f"动态区域 ${desc} 取到空值，无法定位区域")
        if isinstance(region_key, list):
            # 列表变量：展开为多字段 key
            return [str(k) for k in region_key]
        return [str(region_key)]

    def _resolve_min_confidence(self, where) -> float | None:
        """解析 where 子句中的置信度阈值，返回 float 或 None（无 where 子句）"""
        if where is None:
            return None
        val = self._resolve(where.min_confidence)
        if val is None:
            return None
        return float(val)

    def _whole_panel_key(self, scene: str, field_keys, by, verb: str) -> str | None:
        """单一 key 且指向 panel（而非 region）时返回 panel key，否则 None

        region 与 panel 同名时 region 优先（保持既有语义）。
        """
        if not field_keys or len(field_keys) != 1:
            return None
        key = str(field_keys[0])
        if any(r.key == key for r in self._layout.get_scene_regions(scene)):
            return None
        if self._find_panel_in_layout(scene, key) is None:
            return None
        return key

    def _exec_scan(self, node: Scan):
        # PanelRef: panel cell 级 OCR
        if isinstance(node.scene, PanelRef):
            self._scan_panel_cell(node)
            return
        # 解析场景名（可能是 str 或 VarRef）
        scene_ref = node.scene.scene if isinstance(node.scene, SceneRef) else node.scene
        if isinstance(scene_ref, VarRef):
            scene = self.variables.get(scene_ref.name, "")
        else:
            scene = str(scene_ref)
        field_keys = None
        if node.fields:
            field_keys = [self._resolve(f) for f in node.fields]
        elif node.region_var:
            # 动态 region：[scene].$var → 解析变量值
            field_keys = self._dynamic_field_keys(node.region_var)
        var_name = node.target.name if isinstance(node.target, VarRef) else str(node.target)
        min_conf = self._resolve_min_confidence(node.where)

        # 单一 key 指向 panel → 整面板逐格 OCR（$var.[行].[列] 取值）
        panel_key = self._whole_panel_key(scene, field_keys, node.by, "scan")
        if panel_key is not None:
            if node.by is not None:
                # 整面板 + by：返回首个命中的行列 {row, col}
                self._scan_panel_by(scene, panel_key, var_name, node.by, min_confidence=min_conf)
            else:
                self._scan_panel_whole(scene, panel_key, var_name, min_confidence=min_conf)
            return

        if node.by is not None:
            # ── by 子句：短路 OCR，返回字段名 str ──
            by_clause: ByClause = node.by
            target_value = self._resolve(by_clause.target)
            result = self._ensure_workflow().ocr_scene_by(
                scene, field_keys or [], target_value, by_clause.match_mode,
                min_confidence=min_conf,
            )
            self.variables[var_name] = result  # str（命中字段名或 ""）
        else:
            result = self._ensure_workflow().ocr_scene(scene, field_keys, min_confidence=min_conf)
            self.variables[var_name] = result  # dict
            # 存 region 元数据，供 click [scene].$key 解析坐标
            regions = self._layout.get_scene_regions(scene)
            if field_keys:
                regions = [r for r in regions if r.key in field_keys]
            self._coord_meta[var_name] = {r.key: r for r in regions}

    def _exec_recognize(self, node: Recognize):
        """recognize [scene].[f1, f2, ...] as $var [by ...] [group ...] [where ...] — 图像识别场景中的材料"""
        # PanelRef: panel cell 级材料识别
        if isinstance(node.scene, PanelRef):
            self._recognize_panel_cell(node)
            return
        # 解析场景名（可能是 str 或 VarRef）
        scene_ref = node.scene.scene if isinstance(node.scene, SceneRef) else node.scene
        if isinstance(scene_ref, VarRef):
            scene = self.variables.get(scene_ref.name, "")
        else:
            scene = str(scene_ref)
        var_name = node.target.name if isinstance(node.target, VarRef) else str(node.target)
        field_keys = None
        if node.fields:
            field_keys = [self._resolve(f) for f in node.fields]
        elif node.region_var:
            # 动态 region：[scene].$var → 解析变量值
            field_keys = self._dynamic_field_keys(node.region_var)

        # 解析可选的 group 子句
        group = None
        if node.group is not None:
            group = self._resolve(node.group)
        min_conf = self._resolve_min_confidence(node.where)

        # 单一 key 指向 panel → 整面板逐格材料识别（$var.[行].[列] 取值）
        panel_key = self._whole_panel_key(scene, field_keys, node.by, "recognize")
        if panel_key is not None:
            if node.by is not None:
                # 整面板 + by：返回首个命中的行列 {row, col}
                self._recognize_panel_by(scene, panel_key, var_name, node.by, group=group, min_confidence=min_conf)
            else:
                self._recognize_panel_whole(scene, panel_key, var_name, group=group, min_confidence=min_conf)
            return

        if node.by is not None:
            # ── by 子句：短路参考图匹配，返回 slot 名 str ──
            by_clause: ByClause = node.by
            target_value = self._resolve(by_clause.target)
            result = self._ensure_workflow().recognize_materials_by(
                scene, field_keys or [], target_value, by_clause.match_mode,
                group=group, min_confidence=min_conf,
            )
            self.variables[var_name] = result  # str（命中 slot 名或 ""）
        else:
            result, region_map = self._ensure_workflow().recognize_materials(
                scene, field_keys, group=group, min_confidence=min_conf,
            )
            self.variables[var_name] = result           # {slot_key: "参考图标识"}
            self._coord_meta[var_name] = region_map     # {slot_key: Region}

    def _exec_collect(self, node: Collect):
        """collect $var | field_access | literal [as "label" | as $alias_var] — 将值存入输出 dict"""
        if isinstance(node.source, VarRef):
            var_name = node.source.name
            if var_name not in self.variables:
                logger.warning(f"collect: 变量 ${var_name} 未定义，跳过")
                return
            value = self.variables[var_name]  # 可能是 None（null）
            default_key = var_name
        elif isinstance(node.source, FieldAccess):
            value = self._resolve(node.source)
            # 默认 key 取最后一级字段名（如 session.equipped → "equipped"）
            fn = node.source.field_name
            if isinstance(fn, str):
                default_key = fn
            elif isinstance(fn, Literal):
                default_key = str(fn.value)
            else:
                default_key = self._field_path(node.source)
        elif isinstance(node.source, Literal):
            value = node.source.value
            default_key = "value"
        else:
            logger.warning(f"collect: 不支持的源类型 {type(node.source).__name__}")
            return
        # 解析 key：优先 alias_var（动态），其次 alias（静态），最后 default_key
        if node.alias_var:
            key = self.variables.get(node.alias_var.name, default_key)
        elif node.alias:
            key = node.alias
        else:
            key = default_key
        self.output[key] = value
        logger.debug(f"collect: {key} = {value}")

    def _exec_eval(self, node: Eval):
        # 字面量赋值快捷路径：eval $var = "str" | 123 | -1.5
        if node.func_name == "__literal__":
            lit_val = node.func_args[0].value
            if node.target is not None:
                self.variables[node.target] = lit_val
                logger.debug(f"eval: {node.target} = {lit_val!r}")
            return

        # 空字典初始化：eval $var = {}（兼容旧规则）
        if node.func_name == "__empty_dict__":
            if node.target is not None:
                self.variables[node.target] = {}
                logger.debug(f"eval: {node.target} = {{}}")
            return

        # 字典字面量：eval $var = {"k": v, ...}
        if node.func_name == "__dict__":
            raw_dict = node.func_args[0]
            dict_val = {k: self._resolve(v) for k, v in raw_dict.items()}
            if node.target is not None:
                self.variables[node.target] = dict_val
                logger.debug(f"eval: {node.target} = {dict_val!r}")
            return

        # 列表赋值：eval $var = ["a", "b", $c]
        if node.func_name == "__list__":
            if node.target is not None:
                list_val = [self._resolve(item) for item in node.func_args]
                self.variables[node.target] = list_val
                logger.debug(f"eval: {node.target} = {list_val!r}")
            return

        # 范围元组赋值：eval $var = (1, 2) 或 $var = (1, 2)
        if node.func_name == "__range__":
            if node.target is not None:
                range_val = node.func_args[0].value  # (float, float)
                self.variables[node.target] = range_val
                logger.debug(f"eval: {node.target} = {range_val!r}")
            return

        # 默认值赋值：default $var = value — 仅当变量未从外部传入时才赋值
        if node.func_name == "__default__":
            if node.target is not None and node.target not in self.variables:
                lit_value = node.func_args[0].value
                default_val = self._resolve_literal(lit_value)  # 递归解析 dict/list 内变量
                self.variables[node.target] = default_val
                logger.debug(f"default: {node.target} = {default_val!r}")
            return

        # 表达式赋值：eval $var = $dict.$key | $other_var
        if node.func_name == "__expr__":
            expr_node = node.func_args[0]
            if isinstance(expr_node, FieldAccess):
                val = self._eval_field_raw(expr_node)
            else:
                val = self._resolve(expr_node)
            if node.target is not None:
                self.variables[node.target] = val
                logger.debug(f"eval: {node.target} = {val!r}")
            return

        # 算术表达式赋值：eval $var = $a + $b * 2
        if node.func_name == "__arith__":
            arith_node = node.func_args[0]
            val = self._eval_arith(arith_node) if isinstance(arith_node, ArithOp) else self._resolve_arith(arith_node)
            if node.target is not None:
                self.variables[node.target] = val
                logger.debug(f"eval: {node.target} = {val!r}")
            return

        # 函数调用路径
        result = self._call_func_from_eval(node)

        if node.target is not None:
            self.variables[node.target] = result
            logger.debug(f"eval: {node.target} = {result}")
        else:
            logger.debug(f"eval: {node.func_name}(...) = {result} (丢弃)")

    def _exec_eval_field_assign(self, node: EvalFieldChainAssign):
        """eval $dict.key = value 或 eval session.key = value — 字段赋值

        支持 VarRef 根（$dict.key）和 KeywordRef 根（session.key / context.key）。
        """
        # 解析字段访问链，获取所有字段名
        field_chain = self._extract_field_chain(node.target)
        if not field_chain:
            logger.error("eval_field_assign: 无法解析字段链")
            return

        # 第一个字段是变量名或关键字名
        root_name = field_chain[0]
        # 判断根是关键字还是普通变量
        if root_name == "session":
            dict_var = self.session
        elif root_name == "context":
            dict_var = self.context
        else:
            dict_var = self.variables.get(root_name)
        if not isinstance(dict_var, dict):
            logger.error(f"eval_field_assign: {root_name} 不是字典类型")
            return

        # 遍历到倒数第二个字段，获取父字典
        current = dict_var
        for field_name in field_chain[1:-1]:
            if not isinstance(current, dict):
                logger.error(f"eval_field_assign: 中间字段 {field_name} 不是字典类型")
                return
            next_val = current.get(field_name)
            if next_val is None:
                # 自动创建空字典
                next_val = {}
                current[field_name] = next_val
            current = next_val

        # 最后一个字段是赋值目标
        final_field = field_chain[-1]
        # 解析右侧值：FuncCall 调用函数，空 dict 初始化，其余统一走 _resolve
        # （_resolve 覆盖 VarRef / Literal / FieldAccess，后者用于 $a.b = $c.d.e）
        if isinstance(node.value, FuncCall):
            value = self._call_func(node.value)
        elif isinstance(node.value, dict):
            value = {k: self._resolve(v) for k, v in node.value.items()}
        else:
            value = self._resolve(node.value)
        current[final_field] = value
        logger.debug(f"eval: {'.'.join(field_chain)} = {value!r}")

    def _extract_field_chain(self, node: FieldAccess) -> list:
        """从 FieldAccess 节点提取字段名链

        支持 VarRef / KeywordRef 作为根。返回的链第一个元素是变量名或关键字名。
        """
        chain = []
        current = node
        while isinstance(current, FieldAccess):
            fn = current.field_name
            if isinstance(fn, str):
                chain.append(fn)
            elif isinstance(fn, VarRef):
                # 动态字段名，解析变量值
                chain.append(self.variables.get(fn.name, ""))
            elif isinstance(fn, Literal):
                chain.append(fn.value)
            elif isinstance(fn, KeywordRef):
                chain.append(fn.name)
            current = current.root
        # 最底层是 VarRef（变量名）或 KeywordRef（关键字名）
        if isinstance(current, VarRef):
            chain.append(current.name)
        elif isinstance(current, KeywordRef):
            chain.append(current.name)
        chain.reverse()
        return chain

    def _call_func(self, node: FuncCall):
        """执行函数调用并返回结果"""
        resolved_args = [self._resolve(arg) for arg in node.func_args]
        return self._ensure_workflow().call_function(node.func_name, resolved_args, engine=self)

    def _call_func_from_eval(self, node: Eval):
        """从 Eval 节点执行函数调用"""
        resolved_args = [self._resolve(arg) for arg in node.func_args]
        return self._ensure_workflow().call_function(node.func_name, resolved_args, engine=self)

    def _exec_call_proc(self, node: CallProc):
        """call proc_name($arg1, "arg2", ...) [as $output] — 调用子过程

        变量隔离：save/restore caller variables。
        output 隔离：save/restore caller output，子过程的 collect 写入隔离的 output。
        session/context/_coord_meta 共享引用，不隔离。
        return 在过程中 = 退出过程（捕获 _ReturnSignal）。
        若指定 result_var，则将返回值绑定到该变量。
        若指定 output_var，则将子过程的 output dict 绑定到该变量。
        """
        proc_def = self._procs.get(node.name)
        if proc_def is None:
            logger.error(f"call: 未定义的过程 {node.name}")
            return
        logger.debug(f"--- call {node.name}({len(node.args)} args) ---")
        # 1. 先在调用方作用域中解析参数值
        resolved_args = []
        for i in range(len(proc_def.params)):
            if i < len(node.args):
                resolved_args.append(self._resolve(node.args[i]))
            # 未传参数保持未定义状态（访问时返回 null）
        # 2. 执行过程体（变量/output 隔离）
        return_value, callee_output = self._run_proc(proc_def, resolved_args)
        # 3. 绑定返回值到调用方变量
        if node.result_var is not None:
            self.variables[node.result_var] = return_value
        # 4. 绑定子过程 output 到调用方变量
        if node.output_var is not None:
            self.variables[node.output_var] = callee_output

    def _run_proc(self, proc_def, resolved_args: list):
        """执行过程体并返回 (return_value, callee_output)，变量/output 隔离

        call 语句与 call_subcall（Python 桥）共用的过程执行核心：
        子过程从干净变量表与空 output 开始，结束后恢复调用方快照。
        """
        saved_vars = dict(self.variables)
        saved_output = dict(self.output)
        self.variables = {}  # 子过程从干净变量表开始（作用域隔离）
        self.output = {}  # type: ignore[var-annotated]  # 子过程从空 output 开始
        return_value = None
        try:
            # 绑定参数（使用预解析的值）
            for i, param_name in enumerate(proc_def.params):
                if i < len(resolved_args):
                    self.variables[param_name] = resolved_args[i]
            # 执行过程体（return = 退出过程）
            try:
                self._exec_body(proc_def.body)
            except _ReturnSignal as e:
                return_value = e.value  # 捕获返回值
        finally:
            # 捕获子过程的 output，然后恢复调用方的变量和 output
            callee_output = dict(self.output)
            self.variables = saved_vars
            self.output = saved_output
        return return_value, callee_output

    def _exec_find(self, node: Find):
        """find [scene].[area] as $var by ... [where ...] — 在指定区域或全画布 OCR 搜索目标文字

        与 scan/recognize 共享 scene_target + by_clause 语义。
        未找到时变量存入空字符串 ""（falsy），可用 if $var 判断。
        支持 region 和 panel 作为搜索区域（两者都有矩形坐标，对 find 等价）。
        """
        from ...core.layout_models import Region

        # 解析 by 子句（必填）：匹配模式 + 搜索目标
        by_clause: ByClause = node.by
        match_mode = by_clause.match_mode
        match_target = self._resolve(by_clause.target)
        min_conf = self._resolve_min_confidence(node.where)

        # 解析搜索区域（支持 region 和 panel）
        search_region: Region | None = None
        if node.search_scene is not None and node.search_region is not None:
            # 解析场景名
            if isinstance(node.search_scene, VarRef):
                scene = self.variables.get(node.search_scene.name, "")
            else:
                scene = str(node.search_scene)
            # 解析区域名
            if isinstance(node.search_region, VarRef):
                region_key = self.variables.get(node.search_region.name, "")
            else:
                region_key = str(node.search_region)
            # 先查 region（region 优先），再查 panel
            regions = self._layout.get_scene_regions(scene)
            region_map = {r.key: r for r in regions}
            if region_key in region_map:
                search_region = region_map[region_key]
            else:
                # 尝试作为 panel 查找
                panel_obj = self._find_panel_in_layout(scene, region_key)
                if panel_obj is not None:
                    # Panel 转 Region：只取矩形坐标，忽略 rows/cols
                    search_region = Region(
                        key=panel_obj.key,
                        x_ratio=panel_obj.x_ratio,
                        y_ratio=panel_obj.y_ratio,
                        w_ratio=panel_obj.w_ratio,
                        h_ratio=panel_obj.h_ratio,
                    )
                else:
                    raise WorkflowUserError(
                        f"find: 搜索区域 [{scene}].[{region_key}] 在当前布局未绑定坐标"
                    )

        # 执行搜索
        result = self._ensure_workflow().find_text_in_region(
            match_target, match_mode, search_region,
            min_confidence=min_conf,
        )
        # 结果存入变量：FoundRegion（找到）或 ""（未找到，falsy）
        self.variables[node.var_name] = result
