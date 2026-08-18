"""Compile an imported workbook model into a compact executable program."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .excel_formula import (
    FormulaError,
    FormulaModel,
    column_name,
    column_number,
    parse_formula,
)


@dataclass(frozen=True)
class _Range:
    values: list[Any]


class ProgramCompiler:
    """Partially evaluate Excel formulas, retaining only combat-attribute inputs."""

    def __init__(self, workbook_model: dict[str, Any], bindings: dict[str, dict[str, str]]):
        self.model = workbook_model
        self.formula_model = FormulaModel(workbook_model)
        self.bindings = {
            self.formula_model._canonical(address): binding
            for address, binding in bindings.items()
        }
        self.input_specs: list[dict[str, str]] = []
        self.input_nodes: dict[str, int] = {}
        self.nodes: list[list[Any]] = []
        self.node_index: dict[tuple[Any, ...], int] = {}
        self.cell_nodes: dict[str, int] = {}
        self.active: set[str] = set()

    def compile(self, outputs: dict[str, str]) -> dict[str, Any]:
        output_nodes = {name: self._ref(ref) for name, ref in outputs.items()}
        return self._prune(output_nodes)

    def _prune(self, outputs: dict[str, int]) -> dict[str, Any]:
        """Discard temporary nodes created while constant-folding dead branches."""
        reachable: set[int] = set()

        def visit(index: int) -> None:
            if index in reachable:
                return
            reachable.add(index)
            entry = self.nodes[index]
            if entry[0] not in {"const", "input"}:
                for dependency in entry[1:]:
                    visit(dependency)

        for index in outputs.values():
            visit(index)
        ordered = sorted(reachable)
        node_map = {old: new for new, old in enumerate(ordered)}
        used_inputs = sorted({
            self.nodes[index][1]
            for index in ordered if self.nodes[index][0] == "input"
        })
        input_map = {old: new for new, old in enumerate(used_inputs)}
        compact_nodes = []
        for old in ordered:
            entry = self.nodes[old]
            if entry[0] == "input":
                compact_nodes.append(["input", input_map[entry[1]]])
            elif entry[0] == "const":
                compact_nodes.append(entry)
            else:
                compact_nodes.append([entry[0], *(node_map[index] for index in entry[1:])])
        return {
            "inputs": [self.input_specs[index] for index in used_inputs],
            "nodes": compact_nodes,
            "outputs": {name: node_map[index] for name, index in outputs.items()},
        }

    def _node(self, op: str, *args: Any) -> int:
        key = (op, *self._freeze(args))
        cached = self.node_index.get(key)
        if cached is not None:
            return cached
        index = len(self.nodes)
        self.nodes.append([op, *args])
        self.node_index[key] = index
        return index

    @classmethod
    def _freeze(cls, value: Any) -> tuple[Any, ...]:
        if isinstance(value, (list, tuple)):
            return tuple(cls._freeze(item) if isinstance(item, (list, tuple)) else item for item in value)
        return (value,)

    def _constant(self, value: Any) -> int:
        return self._node("const", value)

    def _input(self, address: str) -> int:
        node = self.input_nodes.get(address)
        if node is not None:
            return node
        input_index = len(self.input_specs)
        self.input_specs.append(self.bindings[address])
        node = self._node("input", input_index)
        self.input_nodes[address] = node
        return node

    def _ref(self, address: str, current_sheet: str | None = None) -> int:
        key = self.formula_model._canonical(address, current_sheet)
        if key in self.bindings:
            return self._input(key)
        if key in self.cell_nodes:
            return self.cell_nodes[key]
        if key in self.active:
            raise FormulaError(f"circular reference at {key}")
        sheet, coordinate = key.rsplit("!", 1)
        cell = self.model["sheets"].get(sheet, {}).get("cells", {}).get(coordinate)
        if cell is None:
            result = self._constant(0)
        elif "formula" not in cell:
            result = self._constant(cell.get("value", 0))
        else:
            self.active.add(key)
            try:
                result = self._scalar(self._expr(parse_formula(cell["formula"]), sheet))
            finally:
                self.active.remove(key)
        self.cell_nodes[key] = result
        return result

    def _expr(self, ast: dict[str, Any], sheet: str) -> int | _Range:
        op = ast["op"]
        if op == "literal":
            return self._constant(ast["value"])
        if op == "ref":
            ref = ast["value"]
            if ":" in ref.rsplit("!", 1)[-1]:
                return self._range(ref, sheet)
            return self._ref(ref, sheet)
        if op == "unary":
            arg = self._scalar(self._expr(ast["arg"], sheet))
            return self._emit("pos" if ast["operator"] == "+" else "neg", [arg])
        if op == "binary":
            left = self._scalar(self._expr(ast["left"], sheet))
            right = self._scalar(self._expr(ast["right"], sheet))
            names = {
                "+": "add", "-": "sub", "*": "mul", "/": "div", "^": "pow",
                "&": "concat", "=": "eq", "<>": "ne", "<": "lt", ">": "gt",
                "<=": "le", ">=": "ge",
            }
            return self._emit(names[ast["operator"]], [left, right])
        if op != "call":
            raise FormulaError(f"unsupported AST node {op}")
        return self._call(ast["name"].removeprefix("_XLFN."), ast["args"], sheet)

    @staticmethod
    def _scalar(value: int | _Range) -> int:
        if isinstance(value, _Range):
            raise FormulaError("range used where scalar was required")
        return value

    def _call(self, name: str, args: list[dict[str, Any]], sheet: str) -> int | _Range:
        if name == "VLOOKUP":
            return self._compile_vlookup_ast(args, sheet)
        if name == "IF":
            if len(args) not in (2, 3):
                raise FormulaError("IF expects two or three arguments")
            condition = self._scalar(self._expr(args[0], sheet))
            fixed, value = self._const_value(condition)
            if fixed:
                selected = args[1] if bool(value) else (
                    args[2] if len(args) == 3 else {"op": "literal", "value": False}
                )
                return self._expr(selected, sheet)
            when_true = self._scalar(self._expr(args[1], sheet))
            when_false = self._scalar(self._expr(args[2], sheet)) if len(args) == 3 else self._constant(False)
            return self._emit("if", [condition, when_true, when_false])
        if name == "IFERROR":
            if len(args) != 2:
                raise FormulaError("IFERROR expects two arguments")
            try:
                primary = self._scalar(self._expr(args[0], sheet))
            except (ArithmeticError, FormulaError, TypeError, ValueError):
                return self._expr(args[1], sheet)
            fallback = self._scalar(self._expr(args[1], sheet))
            return self._emit("iferror", [primary, fallback])
        compiled = [self._expr(arg, sheet) for arg in args]
        if name in {"SUM", "MIN", "MAX", "OR"}:
            flat: list[int] = []
            for value in compiled:
                if isinstance(value, _Range):
                    self._flatten_range(value.values, flat)
                else:
                    flat.append(value)
            return self._emit(name.lower(), flat)
        if name == "XLOOKUP":
            # Lookup tables in supplied workbooks are fixed scenario data. They must
            # resolve during import. The selected return cell may remain dynamic.
            return self._compile_lookup(name, compiled)
        raise FormulaError(f"unsupported function {name}")

    def _compile_vlookup_ast(self, args: list[dict[str, Any]], sheet: str) -> int | _Range:
        """Compile VLOOKUP without expanding every column of a wide Excel range."""
        if len(args) < 3 or args[1].get("op") != "ref":
            raise FormulaError("VLOOKUP requires a fixed table range")
        needle_node = self._scalar(self._expr(args[0], sheet))
        column_node = self._scalar(self._expr(args[2], sheet))
        is_constant, column_value = self._const_value(column_node)
        if not is_constant:
            raise FormulaError("VLOOKUP column depends on runtime input")
        match_node: int | None = None
        if len(args) >= 4:
            match_node = self._scalar(self._expr(args[3], sheet))
            fixed, _ = self._const_value(match_node)
            if not fixed:
                raise FormulaError("VLOOKUP match mode depends on runtime input")
        table_sheet, first_col, first_row, _, last_row = self._range_bounds(
            args[1]["value"], sheet,
        )
        target_col = first_col + int(column_value) - 1
        lookup = _Range([
            self._ref(f"{table_sheet}!{column_name(first_col)}{row}")
            for row in range(first_row, last_row + 1)
        ])
        returns = _Range([
            self._ref(f"{table_sheet}!{column_name(target_col)}{row}")
            for row in range(first_row, last_row + 1)
        ])
        lookup_args: list[int | _Range] = [
            needle_node, _Range([[key, value] for key, value in zip(
                lookup.values, returns.values, strict=True,
            )]), self._constant(2),
        ]
        if match_node is not None:
            lookup_args.append(match_node)
        return self._compile_lookup("VLOOKUP", lookup_args)

    def _range_bounds(
        self, reference: str, current_sheet: str,
    ) -> tuple[str, int, int, int, int]:
        if "!" in reference:
            raw_sheet, coordinates = reference.rsplit("!", 1)
            sheet = raw_sheet.strip("'").replace("''", "'")
        else:
            sheet, coordinates = current_sheet, reference
        coordinates = coordinates.replace("$", "").upper()
        dimensions = self.model["sheets"][sheet]["dimensions"]
        start, end = coordinates.split(":", 1)
        if start.isalpha() and end.isalpha():
            return sheet, column_number(start), 1, column_number(end), dimensions["max_row"]
        import re
        pattern = re.compile(r"([A-Z]{1,3})(\d+)")
        sm, em = pattern.fullmatch(start), pattern.fullmatch(end)
        if sm is None or em is None:
            raise FormulaError(f"invalid range {reference}")
        return (
            sheet, column_number(sm.group(1)), int(sm.group(2)),
            column_number(em.group(1)), int(em.group(2)),
        )

    def _range(self, reference: str, current_sheet: str) -> _Range:
        sheet, first_col, first_row, last_col, last_row = self._range_bounds(
            reference, current_sheet,
        )
        rows = [
            [self._ref(f"{sheet}!{column_name(col)}{row}") for col in range(first_col, last_col + 1)]
            for row in range(first_row, last_row + 1)
        ]
        return _Range([row[0] for row in rows] if first_col == last_col else rows)

    @classmethod
    def _flatten_range(cls, values: list[Any], target: list[int]) -> None:
        for value in values:
            if isinstance(value, list):
                cls._flatten_range(value, target)
            else:
                target.append(value)

    def _const_value(self, node: int) -> tuple[bool, Any]:
        entry = self.nodes[node]
        return (True, entry[1]) if entry[0] == "const" else (False, None)

    def _emit(self, op: str, args: list[int]) -> int:
        constants = [self._const_value(arg) for arg in args]
        if all(item[0] for item in constants):
            try:
                return self._constant(evaluate_operation(op, [item[1] for item in constants]))
            except (ArithmeticError, FormulaError, TypeError, ValueError):
                if op != "iferror":
                    raise
        return self._node(op, *args)

    def _compile_lookup(self, name: str, args: list[int | _Range]) -> int | _Range:
        def constant(node: int) -> Any:
            is_constant, value = self._const_value(node)
            if not is_constant:
                raise FormulaError(f"{name} lookup key depends on runtime input")
            return value

        if name == "VLOOKUP":
            if len(args) < 3 or not isinstance(args[1], _Range):
                raise FormulaError("VLOOKUP requires a fixed table range")
            needle = constant(self._scalar(args[0]))
            column = int(constant(self._scalar(args[2])))
            exact = len(args) < 4 or not bool(constant(self._scalar(args[3])))
            candidate: list[int] | None = None
            for raw_row in args[1].values:
                row = raw_row if isinstance(raw_row, list) else [raw_row]
                first = constant(row[0])
                if first == needle:
                    return row[column - 1]
                if not exact and FormulaModel._compare(first, needle, "<="):
                    candidate = row
            if candidate is not None:
                return candidate[column - 1]
            raise FormulaError(f"VLOOKUP could not find {needle!r}")
        if len(args) not in (3, 4) or not isinstance(args[1], _Range) or not isinstance(args[2], _Range):
            raise FormulaError("XLOOKUP requires fixed lookup and return ranges")
        raw_needles = args[0].values if isinstance(args[0], _Range) else [args[0]]
        if len(raw_needles) == 1 and isinstance(raw_needles[0], list):
            raw_needles = raw_needles[0]
        needles = [constant(self._scalar(item)) for item in raw_needles]
        lookup = args[1].values
        returns = args[2].values
        if len(lookup) == 1 and isinstance(lookup[0], list):
            lookup = lookup[0]
        if len(returns) == 1 and isinstance(returns[0], list):
            returns = returns[0]
        lookup_values = [constant(item) for item in lookup]
        results: list[int] = []
        for needle in needles:
            try:
                index = lookup_values.index(needle)
                result = returns[index]
                if isinstance(result, list):
                    raise FormulaError("XLOOKUP returned a nested array")
                results.append(result)
            except (ValueError, IndexError):
                if len(args) == 4:
                    results.append(self._scalar(args[3]))
                else:
                    raise FormulaError(f"XLOOKUP could not find {needle!r}") from None
        return _Range(results) if isinstance(args[0], _Range) else results[0]


def evaluate_operation(op: str, values: list[Any]) -> Any:
    """Evaluate one compact-program opcode."""
    number = FormulaModel._number
    if op == "pos":
        return number(values[0])
    if op == "neg":
        return -number(values[0])
    if op == "add":
        return number(values[0]) + number(values[1])
    if op == "sub":
        return number(values[0]) - number(values[1])
    if op == "mul":
        return number(values[0]) * number(values[1])
    if op == "div":
        return number(values[0]) / number(values[1])
    if op == "pow":
        return number(values[0]) ** number(values[1])
    if op == "concat":
        return f"{values[0]}{values[1]}"
    if op in {"eq", "ne", "lt", "gt", "le", "ge"}:
        operator = {
            "eq": "=", "ne": "<>", "lt": "<", "gt": ">",
            "le": "<=", "ge": ">=",
        }[op]
        return FormulaModel._compare(values[0], values[1], operator)
    if op == "if":
        return values[1] if bool(values[0]) else values[2]
    if op == "iferror":
        return values[0]
    nums = [
        number(value) for value in values
        if isinstance(value, (int, float, bool))
    ]
    if op == "sum":
        return sum(nums)
    if op == "min":
        return min(nums) if nums else 0
    if op == "max":
        return max(nums) if nums else 0
    if op == "or":
        return any(bool(value) for value in values)
    raise FormulaError(f"unsupported program opcode {op}")


class ProgramRuntime:
    """Evaluate a compact program with positional numeric inputs."""

    def __init__(self, program: dict[str, Any], inputs: list[float]):
        self.program = program
        self.inputs = inputs
        self.cache: dict[int, Any] = {}

    def value(self, node: int) -> Any:
        if node in self.cache:
            return self.cache[node]
        entry = self.program["nodes"][node]
        op = entry[0]
        if op == "const":
            result = entry[1]
        elif op == "input":
            result = self.inputs[entry[1]]
        elif op == "if":
            condition = self.value(entry[1])
            result = self.value(entry[2] if condition else entry[3])
        elif op == "iferror":
            try:
                result = self.value(entry[1])
            except (ArithmeticError, FormulaError, TypeError, ValueError):
                result = self.value(entry[2])
        else:
            result = evaluate_operation(op, [self.value(index) for index in entry[1:]])
        self.cache[node] = result
        return result

    def outputs(self) -> dict[str, float]:
        return {name: float(self.value(node)) for name, node in self.program["outputs"].items()}
