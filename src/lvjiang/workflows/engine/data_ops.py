"""数据指令 Mixin：scan / recognize / collect / eval / call proc"""

from loguru import logger

from ..grammar import (
    Scan, Recognize, Collect, Eval, EvalFieldChainAssign, CallProc,
    SceneRef, PanelRef, VarRef, KeywordRef, Literal, FieldAccess, ByClause,
    FuncCall, ArithOp,
)
from .signals import _ReturnSignal


class _DataOpsMixin:
    """数据指令执行：OCR/识别取数、collect 输出、eval 赋值、过程调用"""

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
            region_key = self._resolve(node.region_var)
            if isinstance(region_key, list):
                # 列表变量：展开为多字段 key
                field_keys = [str(k) for k in region_key]
            else:
                field_keys = [str(region_key)]
        var_name = node.target.name if isinstance(node.target, VarRef) else str(node.target)

        if node.by is not None:
            # ── by 子句：短路 OCR，返回字段名 str ──
            by_clause: ByClause = node.by
            target_value = self._resolve(by_clause.target)
            result = self._ensure_workflow().ocr_scene_by(scene, field_keys or [], target_value, by_clause.match_mode)
            self.variables[var_name] = result  # str（命中字段名或 ""）
        else:
            result = self._ensure_workflow().ocr_scene(scene, field_keys)
            self.variables[var_name] = result  # dict
            # 存 region 元数据，供 click [scene].$key 解析坐标
            regions = self._layout.get_scene_regions(scene)
            if field_keys:
                regions = [r for r in regions if r.key in field_keys]
            self._coord_meta[var_name] = {r.key: r for r in regions}

    def _exec_recognize(self, node: Recognize):
        """recognize [scene].[f1, f2, ...] as $var [by ...] [group ...] — 图像识别场景中的材料"""
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
            region_key = self._resolve(node.region_var)
            if isinstance(region_key, list):
                # 列表变量：展开为多字段 key
                field_keys = [str(k) for k in region_key]
            else:
                field_keys = [str(region_key)]

        # 解析可选的 group 子句
        group = None
        if node.group is not None:
            group = self._resolve(node.group)

        if node.by is not None:
            # ── by 子句：短路参考图匹配，返回 slot 名 str ──
            by_clause: ByClause = node.by
            target_value = self._resolve(by_clause.target)
            result = self._ensure_workflow().recognize_materials_by(scene, field_keys or [], target_value, by_clause.match_mode, group=group)
            self.variables[var_name] = result  # str（命中 slot 名或 ""）
        else:
            result, region_map = self._ensure_workflow().recognize_materials(scene, field_keys, group=group)
            self.variables[var_name] = result           # {slot_key: "参考图标识"}
            self._coord_meta[var_name] = region_map     # {slot_key: Region}

    def _exec_collect(self, node: Collect):
        """collect $var | field_access | literal [as "label" | as $alias_var] — 将值存入输出 dict"""
        if isinstance(node.source, VarRef):
            var_name = node.source.name
            value = self.variables.get(var_name)
            if value is None:
                logger.warning(f"collect: 变量 ${var_name} 未定义，跳过")
                return
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
                default_val = node.func_args[0].value
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
        """call proc_name($arg1, "arg2", ...) — 调用子过程

        变量隔离：save/restore caller variables。
        session/context/_coord_meta 共享引用，不隔离。
        return 在过程中 = 退出过程（捕获 _ReturnSignal）。
        """
        proc_def = self._procs.get(node.name)
        if proc_def is None:
            logger.error(f"call: 未定义的过程 {node.name}")
            return
        logger.debug(f"--- call {node.name}({len(node.args)} args) ---")
        # 1. 保存当前变量快照
        saved_vars = dict(self.variables)
        try:
            # 2. 绑定参数
            for i, param_name in enumerate(proc_def.params):
                if i < len(node.args):
                    self.variables[param_name] = self._resolve(node.args[i])
            # 3. 执行过程体（return = 退出过程）
            try:
                self._exec_body(proc_def.body)
            except _ReturnSignal:
                pass  # 正常退出过程
        finally:
            # 4. 恢复变量（session/context 自然保留修改）
            self.variables = saved_vars
