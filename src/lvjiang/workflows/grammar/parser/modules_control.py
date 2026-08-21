"""_ModuleControlMixin：import/def/call proc 与控制流 if/for/for-range/loop/label/goto"""

from lark import Token

from ..ast_nodes import (
    Break,
    CallProc,
    Continue,
    For,
    ForRange,
    Goto,
    If,
    Import,
    Label,
    Literal,
    Loop,
    ProcDef,
    Return,
    Try,
    UntilLoop,
    VarRef,
    WhileLoop,
)


class _ModuleControlMixin:
    """模块化（import/def/call proc）与控制流（if/for/loop/label/goto）回调"""

    # ─── 模块化：import / def / call proc ─────────────────

    def import_stmt(self, items):
        """import "path.wf" — 引入外部文件的 def 定义"""
        path = self._unquote(str(items[0]))
        return Import(path=path, line_no=self._line(items))

    def def_stmt(self, items):
        """def proc_name($p1, $p2) ... end — 定义子过程

        items 结构: NAME Token, [def_param_list], body_stmts...
        """
        name = str(items[0])
        params = []
        raw_body = []
        for item in items[1:]:
            if isinstance(item, list) and all(isinstance(p, str) for p in item):
                params = item  # def_param_list 返回 list[str]
            elif item is not None and not isinstance(item, Token):
                raw_body.append(item)
        # 展平语法糖产生的列表（click/drag + wait_clause）
        body = self._flatten_body(raw_body)
        return ProcDef(name=name, params=params, body=body)

    def def_param_list(self, items):
        """$p1, $p2 → [str, str]"""
        return [str(p) for p in items]

    def def_param(self, items):
        """$NAME → str(NAME)"""
        return str(items[0])

    def call_proc_stmt(self, items):
        """call proc_name($arg1, "arg2", ...) [as $output] — 调用过程"""
        name = str(items[0])
        args = []
        output_var = None
        for item in items[1:]:
            if isinstance(item, list):
                args = item  # call_arg_list 返回 list
            elif isinstance(item, str) and not isinstance(item, Token):
                # call_output_as 返回 str（变量名）
                output_var = item
            elif item is not None and not isinstance(item, Token):
                args.append(item)
        return CallProc(name=name, args=args, output_var=output_var, line_no=self._line(items))

    def call_proc_assign_stmt(self, items):
        """call $result = proc_name($arg1, "arg2", ...) [as $output] — 调用过程并绑定返回值"""
        # 语法: "call" "$" NAME "=" NAME "(" [call_arg_list] ")" [call_output_clause]
        # items: [result_var_name, proc_name, ...args, output_var?]
        result_var = str(items[0])
        proc_name = str(items[1])
        args = []
        output_var = None
        for item in items[2:]:
            if isinstance(item, list):
                args = item  # call_arg_list 返回 list
            elif isinstance(item, str) and not isinstance(item, Token):
                # call_output_as 返回 str（变量名）
                output_var = item
            elif item is not None and not isinstance(item, Token):
                args.append(item)
        return CallProc(name=proc_name, args=args, result_var=result_var, output_var=output_var, line_no=self._line(items))

    def call_output_as(self, items):
        """as $output → str(变量名)"""
        return str(items[0])

    def call_arg_list(self, items):
        """参数列表 → list"""
        return list(items)

    def call_arg_str(self, items):
        """字符串参数 → Literal"""
        return Literal(value=self._unquote(str(items[0])))

    def call_arg_num(self, items):
        """数字参数 → float"""
        return items[0]

    def call_arg_var(self, items):
        """变量参数 → VarRef"""
        return items[0]

    def call_arg_field(self, items):
        """字段访问参数 → FieldAccess"""
        return items[0]

    # ─── 控制流 ───────────────────────────────────────────

    @staticmethod
    def _flatten_body(items):
        """展平复合语句体中的列表（语法糖 click/drag + wait_clause 展开产生）"""
        result = []
        for item in items:
            if item is None or isinstance(item, Token):
                continue
            if isinstance(item, list):
                result.extend(item)
            else:
                result.append(item)
        return result

    def if_stmt(self, items):
        condition = items[0]
        then_body = []
        else_body = []
        in_else = False
        for item in items[1:]:
            if isinstance(item, tuple) and len(item) == 2 and item[0] == "else":
                in_else = True
                else_body = item[1]  # else_clause 已经过展平
            elif item is not None and not isinstance(item, Token):
                if in_else:
                    else_body.append(item)
                else:
                    then_body.append(item)
        # 展平语法糖产生的列表（click/drag + wait_clause）
        then_body = self._flatten_body(then_body)
        else_body = self._flatten_body(else_body)
        return If(condition=condition, then_body=then_body, else_body=else_body, line_no=self._line(items))

    def else_clause(self, items):
        """返回标记元组，便于 if_stmt 区分 then/else

        支持两种形式：
        - else + 语句块 → ("else", [stmt1, stmt2, ...])
        - else if（elif_clause）→ ("else", [If(...)])
        """
        body = [i for i in items if i is not None and not isinstance(i, Token)]
        return ("else", body)

    def elif_clause(self, items):
        """else if 子句 → 构造嵌套 If 节点，包装为 else body

        items: condition, [body_stmts...], [else_clause]
        返回 ("else", [If(...)]) 供 if_stmt 使用
        """
        condition = items[0]
        then_body = []
        else_body = []
        in_else = False
        for item in items[1:]:
            if isinstance(item, tuple) and len(item) == 2 and item[0] == "else":
                in_else = True
                else_body = item[1]
            elif item is not None and not isinstance(item, Token):
                if in_else:
                    else_body.append(item)
                else:
                    then_body.append(item)
        then_body = self._flatten_body(then_body)
        else_body = self._flatten_body(else_body)
        nested_if = If(condition=condition, then_body=then_body, else_body=else_body, line_no=self._line(items))
        return ("else", [nested_if])

    def for_stmt(self, items):
        var_name = str(items[0])
        iterable = items[1]  # for_iter → list[Literal] | VarRef | FuncCall | ForRange
        body = self._flatten_body(items[2:])
        # 若 for_iter 返回 ForRange，直接设置 body 并返回
        if isinstance(iterable, ForRange):
            return ForRange(var=var_name, start=iterable.start, end=iterable.end,
                           body=body, line_no=self._line(items))
        return For(var=var_name, iterable=iterable, body=body, line_no=self._line(items))

    def for_iter_static(self, items):
        """for_iter: list_literal → list[Literal | VarRef]"""
        return items[0]  # list_literal 已返回 list

    def for_iter_var(self, items):
        """for_iter: var_ref → VarRef"""
        return items[0]  # var_ref 已返回 VarRef

    def for_iter_func(self, items):
        """for_iter: func_call → FuncCall（如 range(1, 100)）"""
        return items[0]  # func_call 已返回 FuncCall

    def for_iter_range(self, items):
        """for_iter: for_range → ForRange（如 [1...100]）"""
        return items[0]  # for_range 已返回 ForRange

    def for_range(self, items):
        """[start...end] 闭区间范围迭代"""
        # items = [start_endpoint, RANGE_OP_token, end_endpoint]
        # 过滤掉 RANGE_OP token
        endpoints = [i for i in items if not isinstance(i, Token) or i.type != 'RANGE_OP']
        return ForRange(var="", start=endpoints[0], end=endpoints[1], line_no=self._line(items))

    def for_range_endpoint(self, items):
        """范围端点：数字或变量引用"""
        return items[0]  # number 返回 float，var_ref 返回 VarRef

    def loop_count_stmt(self, items):
        count_val = items[0]
        if isinstance(count_val, (int, float)):
            count = int(count_val)  # number 规则产出 float，loop 需要 int
        elif isinstance(count_val, VarRef):
            count = count_val  # 变量引用，运行时解析
        elif isinstance(count_val, Token):
            count = int(str(count_val))
        else:
            count = str(count_val)
        body = self._flatten_body(items[1:])
        return Loop(count=count, body=body, line_no=self._line(items))

    def loop_while_stmt(self, items):
        """loop while <condition> ... end — 条件循环（条件为真继续）"""
        condition = items[0]
        body = self._flatten_body(items[1:])
        return WhileLoop(condition=condition, body=body, line_no=self._line(items))

    def loop_until_stmt(self, items):
        """loop until <condition> ... end — 条件循环（条件为真退出）"""
        condition = items[0]
        body = self._flatten_body(items[1:])
        return UntilLoop(condition=condition, body=body, line_no=self._line(items))

    def break_stmt(self, items):
        return Break(line_no=self._line(items))

    def continue_stmt(self, items):
        """continue — 跳过当前迭代"""
        return Continue(line_no=self._line(items))

    def return_stmt(self, items):
        """return [value] — 返回值可选"""
        if not items:
            value = None
        else:
            item = items[0]
            # STRING token 需要解包为 Literal
            if isinstance(item, Token) and item.type == 'STRING':
                value = Literal(value=self._unquote(str(item)))
            else:
                value = item
        return Return(value=value, line_no=self._line(items))

    def label_stmt(self, items):
        return Label(name=str(items[0]), line_no=self._line(items))

    def goto_stmt(self, items):
        return Goto(target=str(items[0]), line_no=self._line(items))

    # ─── 异常处理：try / catch ───────────────────

    def try_stmt(self, items):
        """try ... [catch $err ...] end — 异常处理

        items 结构: [body_stmts...], [catch_clause_tuple]
        catch_clause_tuple 形如 ("catch", err_var|None, catch_body_list)
        """
        body = []
        catch_body = []
        err_var = None
        for item in items:
            if item is None or isinstance(item, Token):
                continue
            if isinstance(item, tuple) and len(item) >= 1 and item[0] == "catch":
                # catch_clause 返回的元组
                err_var = item[1] if len(item) > 1 else None
                catch_body = item[2] if len(item) > 2 else []
            elif isinstance(item, list):
                # transformer 可能把语句体包装为列表，展平
                body.extend(item)
            else:
                body.append(item)
        body = self._flatten_body(body)
        catch_body = self._flatten_body(catch_body)
        return Try(body=body, catch_body=catch_body, err_var=err_var, line_no=self._line(items))

    def catch_clause(self, items):
        """catch [$err] ... — 返回元组供 try_stmt 消费

        items 结构: [var_ref]?, body_stmts...
        """
        err_var = None
        body = []
        for item in items:
            if item is None or isinstance(item, Token):
                continue
            if isinstance(item, VarRef):
                err_var = item.name
            elif isinstance(item, list):
                body.extend(item)
            else:
                body.append(item)
        body = self._flatten_body(body)
        return ("catch", err_var, body)
