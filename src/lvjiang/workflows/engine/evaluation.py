"""条件求值与变量解析 Mixin"""

from typing import Any

from loguru import logger

from ..grammar import (
    And,
    ArithOp,
    Contains,
    Equals,
    FieldAccess,
    FuncCall,
    GreaterEqual,
    GreaterThan,
    InList,
    IsEmpty,
    KeywordRef,
    Label,
    LessEqual,
    LessThan,
    Literal,
    Not,
    NotEqual,
    NumericEqual,
    Or,
    VarRef,
)
from .signals import WorkflowUserError

# 数值相等容差：== / != 统一用容差比较，避免浮点误差（如 0.1+0.2 != 0.3）
_NUM_EQ_EPSILON = 1e-9


class _EvalMixin:
    """条件求值 / 变量与表达式解析 / 坐标与工具方法"""

    # ─── 条件求值 ─────────────────────────────────────────

    @staticmethod
    def _str_or_empty(val) -> str:
        """将值转为字符串，null 视为空字符串"""
        return "" if val is None else str(val)

    def _eval_condition(self, node) -> bool:
        """递归求值条件表达式 AST 节点"""
        match node:
            case Contains():
                left = self._eval_var_or_field(node.left)
                right = self._resolve(node.right)
                return self._str_or_empty(right) in left if left else False
            case Equals():
                left = self._eval_var_or_field(node.left)
                right = self._resolve(node.right)
                return left == self._str_or_empty(right)
            case InList():
                left = self._eval_var_or_field(node.left)
                right = [self._str_or_empty(self._resolve(item)) for item in node.right]
                return left in right if left else False
            case IsEmpty():
                left = self._eval_var_or_field(node.expr)
                return not left or left.strip() == ""
            case GreaterThan():
                left = self._resolve_arith(node.left)
                right = self._resolve_arith(node.right)
                return left > right if left is not None and right is not None else False
            case LessThan():
                left = self._resolve_arith(node.left)
                right = self._resolve_arith(node.right)
                return left < right if left is not None and right is not None else False
            case GreaterEqual():
                left = self._resolve_arith(node.left)
                right = self._resolve_arith(node.right)
                return left >= right if left is not None and right is not None else False
            case LessEqual():
                left = self._resolve_arith(node.left)
                right = self._resolve_arith(node.right)
                return left <= right if left is not None and right is not None else False
            case NotEqual():
                left = self._resolve_arith(node.left)
                right = self._resolve_arith(node.right)
                if left is None or right is None:
                    return True
                return abs(left - right) >= _NUM_EQ_EPSILON
            case NumericEqual():
                left = self._resolve_arith(node.left)
                right = self._resolve_arith(node.right)
                if left is None or right is None:
                    return False
                return abs(left - right) < _NUM_EQ_EPSILON
            case Not():
                return not self._eval_condition(node.operand)
            case And():
                return self._eval_condition(node.left) and self._eval_condition(node.right)
            case Or():
                return self._eval_condition(node.left) or self._eval_condition(node.right)
            case VarRef():
                # 条件中的 $var → truthy 检查
                val = self.variables.get(node.name)
                return bool(val)
            case _:
                logger.error(f"未知条件节点: {type(node).__name__}")
                return False

    @staticmethod
    def _to_number(val: str):
        """将字符串转为数值，失败时返回 None"""
        try:
            return float(val)
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _field_path(node) -> str:
        """生成字段访问路径的调试描述：$ring.affix_1.value → 'ring.affix_1.value'
        支持 VarRef / KeywordRef 作为根。
        """
        # 裸变量/关键字直接返回名称
        if isinstance(node, VarRef):
            return node.name
        if isinstance(node, KeywordRef):
            return node.name
        parts = []
        current = node
        while isinstance(current, FieldAccess):
            fn = current.field_name
            if isinstance(fn, VarRef):
                parts.append(f"${fn.name}")
            elif isinstance(fn, KeywordRef):
                parts.append(fn.name)
            elif isinstance(fn, Literal):
                parts.append(f'"{fn.value}"')
            else:
                parts.append(str(fn))
            current = current.root
        if isinstance(current, VarRef):
            parts.append(current.name)
        elif isinstance(current, KeywordRef):
            parts.append(current.name)
        return ".".join(reversed(parts))

    def _eval_field_access(self, node: FieldAccess) -> str:
        """求值字段访问链：$var.f1.f2.f3 → 逐层遍历，返回字符串"""
        val = self._eval_field_raw(node)
        return "" if val is None else str(val)

    def _eval_var_or_field(self, node) -> str:
        """求值变量或字段访问：支持 VarRef 和 FieldAccess"""
        if isinstance(node, VarRef):
            val = self.variables.get(node.name)
            return "" if val is None else str(val)
        elif isinstance(node, FieldAccess):
            return self._eval_field_access(node)
        return ""

    def _eval_field_raw(self, node: FieldAccess):
        """求值字段访问链，返回原始值（dict/int/float/str 等）

        中间层返回 dict/list 以便继续链式访问，叶子层返回具体值。
        root 支持 VarRef / KeywordRef / FieldAccess。
        field_name 支持四种类型：
          str      → 静态 dict key（裸 NAME）
          VarRef   → 动态 key（变量解析后查 dict / 按 index 取 list）
          Literal  → 静态字面量 key（来自 $var."key" 或 $var.[key]）
          KeywordRef → 关键字引用（嵌套 session/context 访问）
        """
        # 先解析 root
        if isinstance(node.root, VarRef):
            current = self.variables.get(node.root.name)
        elif isinstance(node.root, KeywordRef):
            current = self._resolve(node.root)
        elif isinstance(node.root, FieldAccess):
            current = self._eval_field_raw(node.root)
        else:
            return ""

        # 解析当前层 key
        if isinstance(node.field_name, str):
            key = node.field_name
        elif isinstance(node.field_name, VarRef):
            key = self.variables.get(node.field_name.name, "")
        elif isinstance(node.field_name, Literal):
            key = node.field_name.value
        elif isinstance(node.field_name, KeywordRef):
            # field_access.session / field_access.context（罕见但合法）
            return self._resolve(node.field_name)
        else:
            return ""

        # dict 按 key 取
        if isinstance(current, dict):
            if key in current:
                return current[key]
            # 数值 key 归一化：整面板扫描结果以 "1"/"2" 字符串为 key，
            # 而 $var.$r 中 $r 来自 for 循环时是 int，转 str(int) 后再查一次
            try:
                return current.get(str(int(float(key))))
            except (TypeError, ValueError):
                return None  # 缺失 key → None（null）
        # list 按 index 取（key 需为整数）
        if isinstance(current, list):
            try:
                idx = int(key)
                return current[idx] if 0 <= idx < len(current) else None
            except (ValueError, TypeError):
                return None
        # str 类型不支持字段访问（by 子句返回 str，用户误用 .field 时应报错）
        if isinstance(current, str):
            var_desc = self._field_path(node)
            raise WorkflowUserError(
                f"${var_desc} 的值是字符串类型（{current!r}），"
                f"不能使用 .{key} 访问字段。"
                f"by 子句返回的是字段名（str），不是 dict。"
            )
        return None

    # ─── 变量解析 ─────────────────────────────────────────

    def _resolve(self, node) -> Any:
        """解析表达式节点为运行时值

        VarRef → 查变量表（找不到返回 None 即 null）
        KeywordRef → 返回 session/context 字典引用
        Literal → 直接返回值
        FieldAccess → 逐层遍历字典/列表
        ArithOp → 算术表达式求值
        int/float → 直接返回（来自 grammar number 规则）
        str/Token → 直接返回字符串（来自 grammar STRING）
        list 类型变量原样返回（支持 for 迭代）
        """
        match node:
            case VarRef():
                return self.variables.get(node.name)  # 未定义 → None（null）
            case KeywordRef():
                if node.name == "session":
                    return self.session
                if node.name == "context":
                    return self.context
                return {}
            case Literal():
                return node.value
            case FieldAccess():
                return self._eval_field_raw(node)
            case ArithOp():
                return self._eval_arith(node)
            case FuncCall():
                return self._call_func(node)
            case int() | float():
                return node
            case str():
                return node  # STRING token 已解包为 str
            case dict():
                return {k: self._resolve(v) for k, v in node.items()}
            case list():
                return [self._resolve(item) for item in node]
            case _:
                return None

    def _resolve_arith(self, node) -> float | None:
        """解析算术表达式右侧为数值（用于条件比较）

        支持：float 字面量、VarRef、FieldAccess、ArithOp
        """
        if isinstance(node, (int, float)):
            return float(node)
        val = self._resolve(node)
        return self._to_number(val)

    def _eval_arith(self, node: ArithOp) -> float:
        """求值算术表达式节点

        递归求值 left/right，统一转 float 后执行运算。
        null 操作数视为 0.0，除法为浮点除，除 0 返回 0。
        """
        left = self._resolve_arith(node.left)
        right = self._resolve_arith(node.right)
        # null 操作数视为 0.0
        if left is None:
            left = 0.0
        if right is None:
            right = 0.0
        match node.op:
            case "+":
                return left + right
            case "-":
                return left - right
            case "*":
                return left * right
            case "/":
                return left / right if right != 0 else 0.0
            case _:
                logger.warning(f"未知算术运算符: {node.op}")
                return 0.0

    def _resolve_param(self, node) -> str:
        """解析 click/scan 参数

        VarRef → 变量优先，回退字面量
        Literal → 直接返回
        """
        return self._resolve(node)

    def _resolve_var_name(self, node) -> str:
        """提取变量名（用于 scan as / collect_as 等赋值目标）"""
        if isinstance(node, VarRef):
            return node.name
        return str(node)

    def _resolve_duration(self, node) -> float | tuple[float, float]:
        """解析拖拽时长：Literal → float，list[Literal] → tuple"""
        if isinstance(node, list):
            return (float(node[0].value), float(node[1].value))
        if isinstance(node, Literal):
            return float(node.value)
        return float(node)

    def _coord_ratio_to_screen(self, rx: float, ry: float) -> tuple[int, int]:
        """画布归一化坐标 (rx, ry) → 屏幕绝对坐标

        与 _region_to_screen / _point_to_screen 同源的坐标转换链：
        屏幕 = 窗口偏移 + 画布原点 + 归一化比例 × 画布尺寸。
        窗口缩放/移动后回放仍准确。
        """
        w, h = self._capture.get_capture_size()
        canvas = self._layout.get_canvas()
        canvas_px_x = canvas.x_ratio * w
        canvas_px_y = canvas.y_ratio * h
        canvas_px_w = canvas.w_ratio * w
        canvas_px_h = canvas.h_ratio * h
        cx = canvas_px_x + rx * canvas_px_w
        cy = canvas_px_y + ry * canvas_px_h
        return int(self._window_left + cx), int(self._window_top + cy)

    # ─── coord_meta 查找 ─────────────────────────────────────

    def _find_region_in_coord_meta(self, key: str):
        """在所有 coord_meta 条目中查找 key 对应的 Region

        scan/recognize 会将 {key: Region} 存入 coord_meta，
        此方法遍历所有条目，找到第一个匹配的 Region。
        """
        for _var_name, region_map in self._coord_meta.items():
            if isinstance(region_map, dict) and key in region_map:
                return region_map[key]
        return None

    # ─── 工具 ─────────────────────────────────────────────

    @staticmethod
    def _build_label_index(stmts: list) -> dict[str, int]:
        """预扫描语句列表，建立 label → 索引 映射"""
        index = {}
        for i, stmt in enumerate(stmts):
            if isinstance(stmt, Label):
                index[stmt.name] = i
        return index

    @staticmethod
    def _cond_desc(node) -> str:
        """条件的调试描述"""
        match node:
            case Contains():
                return f"{_EvalMixin._field_path(node.left)} contains {node.right}"
            case Equals():
                return f"{_EvalMixin._field_path(node.left)} equals {node.right}"
            case InList():
                return f"{_EvalMixin._field_path(node.left)} in [...]"
            case IsEmpty():
                return f"{_EvalMixin._field_path(node.expr)} is_empty"
            case GreaterThan():
                return f"{_EvalMixin._field_path(node.left)} > {node.right}"
            case LessThan():
                return f"{_EvalMixin._field_path(node.left)} < {node.right}"
            case GreaterEqual():
                return f"{_EvalMixin._field_path(node.left)} >= {node.right}"
            case LessEqual():
                return f"{_EvalMixin._field_path(node.left)} <= {node.right}"
            case NotEqual():
                return f"{_EvalMixin._field_path(node.left)} != {node.right}"
            case NumericEqual():
                return f"{_EvalMixin._field_path(node.left)} == {node.right}"
            case Not():
                return f"not ({_EvalMixin._cond_desc(node.operand)})"
            case And():
                return f"({_EvalMixin._cond_desc(node.left)} and {_EvalMixin._cond_desc(node.right)})"
            case Or():
                return f"({_EvalMixin._cond_desc(node.left)} or {_EvalMixin._cond_desc(node.right)})"
            case VarRef():
                return f"[{node.name}]"
            case _:
                return str(type(node).__name__)
