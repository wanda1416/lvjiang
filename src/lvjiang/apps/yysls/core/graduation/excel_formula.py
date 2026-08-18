"""Small, deterministic evaluator for the Excel subset used by graduation models.

The evaluator intentionally supports only scalar arithmetic, comparisons, cell/range
references and the functions present in the source workbooks.  It never calls eval().
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Iterable


class FormulaError(ValueError):
    """Raised when a model contains unsupported or invalid Excel syntax."""


_TOKEN_RE = re.compile(
    r"\s*(?:"
    r'(?P<string>"(?:[^"]|"")*")|'
    r"(?P<ref>(?:(?:'[^']+'|[^\s()+\-*/^&=<>%,]+)!)?"
    r"(?:\$?[A-Z]{1,3}\$?\d+(?::\$?[A-Z]{1,3}\$?\d+)?|"
    r"\$?[A-Z]{1,3}:\$?[A-Z]{1,3}))|"
    r"(?P<number>(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][+-]?\d+)?%?)|"
    r"(?P<op><>|<=|>=|[+\-*/^&=<>(),])|"
    r"(?P<name>[A-Za-z_][A-Za-z0-9_.]*)"
    r")"
)


@dataclass(frozen=True)
class Token:
    kind: str
    value: str


def tokenize(formula: str) -> list[Token]:
    text = formula[1:] if formula.startswith("=") else formula
    result: list[Token] = []
    pos = 0
    while pos < len(text):
        match = _TOKEN_RE.match(text, pos)
        if not match:
            raise FormulaError(f"unsupported formula syntax near {text[pos:pos + 30]!r}")
        kind = match.lastgroup
        if kind is None:
            raise FormulaError("empty formula token")
        result.append(Token(kind, match.group(kind)))
        pos = match.end()
    result.append(Token("eof", ""))
    return result


class FormulaParser:
    """Pratt parser returning a compact JSON-compatible AST."""

    _PRECEDENCE = {
        "=": 1, "<>": 1, "<": 1, ">": 1, "<=": 1, ">=": 1,
        "&": 2, "+": 3, "-": 3, "*": 4, "/": 4, "^": 5,
    }

    def __init__(self, formula: str):
        self.tokens = tokenize(formula)
        self.index = 0

    @property
    def current(self) -> Token:
        return self.tokens[self.index]

    def take(self) -> Token:
        token = self.current
        self.index += 1
        return token

    def parse(self) -> dict[str, Any]:
        node = self._expr(0)
        if self.current.kind != "eof":
            raise FormulaError(f"unexpected token {self.current.value!r}")
        return node

    def _expr(self, minimum: int) -> dict[str, Any]:
        token = self.take()
        left: dict[str, Any]
        if token.kind == "op" and token.value in ("+", "-"):
            left = {"op": "unary", "operator": token.value, "arg": self._expr(6)}
        elif token.kind == "number":
            percent = token.value.endswith("%")
            value = float(token.value[:-1] if percent else token.value)
            left = {"op": "literal", "value": value / 100 if percent else value}
        elif token.kind == "string":
            left = {"op": "literal", "value": token.value[1:-1].replace('""', '"')}
        elif token.kind == "ref":
            left = {"op": "ref", "value": token.value}
        elif token.kind == "name":
            name = token.value.upper()
            if self.current.value == "(":
                self.take()
                args = []
                if self.current.value != ")":
                    while True:
                        args.append(self._expr(0))
                        if self.current.value != ",":
                            break
                        self.take()
                if self.take().value != ")":
                    raise FormulaError("missing closing parenthesis")
                left = {"op": "call", "name": name, "args": args}
            elif name in ("TRUE", "FALSE"):
                left = {"op": "literal", "value": name == "TRUE"}
            else:
                raise FormulaError(f"unsupported name {token.value!r}")
        elif token.value == "(":
            left = self._expr(0)
            if self.take().value != ")":
                raise FormulaError("missing closing parenthesis")
        else:
            raise FormulaError(f"unexpected token {token.value!r}")

        while self.current.kind == "op" and self.current.value in self._PRECEDENCE:
            operator = self.current.value
            precedence = self._PRECEDENCE[operator]
            if precedence < minimum:
                break
            self.take()
            right = self._expr(precedence if operator == "^" else precedence + 1)
            left = {"op": "binary", "operator": operator, "left": left, "right": right}
        return left


@lru_cache(maxsize=100_000)
def parse_formula(formula: str) -> dict[str, Any]:
    return FormulaParser(formula).parse()


_CELL_RE = re.compile(r"^\$?([A-Z]{1,3})\$?(\d+)$")
_COL_RANGE_RE = re.compile(r"^\$?([A-Z]{1,3}):\$?([A-Z]{1,3})$")


def column_number(name: str) -> int:
    result = 0
    for char in name:
        result = result * 26 + ord(char) - 64
    return result


def column_name(number: int) -> str:
    result = ""
    while number:
        number, remainder = divmod(number - 1, 26)
        result = chr(65 + remainder) + result
    return result


class FormulaModel:
    """Evaluate cells in a converted graduation workbook model."""

    def __init__(self, model: dict[str, Any], overrides: dict[str, Any] | None = None):
        self.model = model
        self.sheets = model["sheets"]
        self.overrides = {self._canonical(k): v for k, v in (overrides or {}).items()}
        self.cache: dict[str, Any] = {}
        self.active: set[str] = set()
        self.ast_cache: dict[str, dict[str, Any]] = {}
        self.range_cache: dict[str, list[Any]] = {}

    @staticmethod
    def _canonical(address: str, current_sheet: str | None = None) -> str:
        if "!" in address:
            sheet, coord = address.rsplit("!", 1)
            sheet = sheet.strip("'").replace("''", "'")
        elif current_sheet is not None:
            sheet, coord = current_sheet, address
        else:
            raise FormulaError(f"reference lacks sheet: {address}")
        return f"{sheet}!{coord.replace('$', '').upper()}"

    def value(self, address: str, current_sheet: str | None = None) -> Any:
        key = self._canonical(address, current_sheet)
        if key in self.overrides:
            return self.overrides[key]
        if key in self.cache:
            return self.cache[key]
        if key in self.active:
            raise FormulaError(f"circular reference at {key}")
        sheet, coord = key.rsplit("!", 1)
        cell = self.sheets.get(sheet, {}).get("cells", {}).get(coord)
        if cell is None:
            self.cache[key] = 0
            return 0
        if "formula" not in cell:
            return cell.get("value", 0)
        self.active.add(key)
        try:
            formula = cell["formula"]
            ast = self.ast_cache.get(formula)
            if ast is None:
                ast = parse_formula(formula)
                self.ast_cache[formula] = ast
            result = self._eval(ast, sheet)
            self.cache[key] = result
            return result
        finally:
            self.active.remove(key)

    def _eval(self, node: dict[str, Any], sheet: str) -> Any:
        op = node["op"]
        if op == "literal":
            return node["value"]
        if op == "ref":
            ref = node["value"]
            if ":" in ref.rsplit("!", 1)[-1]:
                return list(self._range(ref, sheet))
            return self.value(ref, sheet)
        if op == "unary":
            value = self._number(self._eval(node["arg"], sheet))
            return value if node["operator"] == "+" else -value
        if op == "binary":
            left = self._eval(node["left"], sheet)
            right = self._eval(node["right"], sheet)
            operator = node["operator"]
            if operator in ("=", "<>", "<", ">", "<=", ">="):
                return self._compare(left, right, operator)
            if operator == "&":
                return f"{left}{right}"
            a, b = self._number(left), self._number(right)
            return {"+": lambda: a + b, "-": lambda: a - b,
                    "*": lambda: a * b, "/": lambda: a / b,
                    "^": lambda: a ** b}[operator]()
        if op == "call":
            return self._call(node["name"], node["args"], sheet)
        raise FormulaError(f"unsupported AST node {op}")

    def _call(self, name: str, args: list[dict[str, Any]], sheet: str) -> Any:
        name = name.removeprefix("_XLFN.")
        if name == "IF":
            if len(args) not in (2, 3):
                raise FormulaError("IF expects two or three arguments")
            return self._eval(args[1], sheet) if self._truth(self._eval(args[0], sheet)) else (
                self._eval(args[2], sheet) if len(args) == 3 else False)
        if name == "IFERROR":
            if len(args) != 2:
                raise FormulaError("IFERROR expects two arguments")
            try:
                return self._eval(args[0], sheet)
            except (FormulaError, ArithmeticError):
                return self._eval(args[1], sheet)
        if name == "OR":
            return any(self._truth(self._eval(arg, sheet)) for arg in args)
        if name in ("MIN", "MAX", "SUM"):
            values = []
            for arg in args:
                value = self._eval(arg, sheet)
                if isinstance(value, list):
                    for item in value:
                        values.extend(item if isinstance(item, list) else [item])
                else:
                    values.append(value)
            numbers = [self._number(v) for v in values
                       if isinstance(v, (int, float, bool))]
            if name == "SUM":
                return sum(numbers)
            if not numbers:
                return 0
            return min(numbers) if name == "MIN" else max(numbers)
        if name == "VLOOKUP":
            if len(args) < 3:
                raise FormulaError("VLOOKUP expects at least three arguments")
            needle = self._eval(args[0], sheet)
            table = self._eval(args[1], sheet)
            column = int(self._number(self._eval(args[2], sheet)))
            exact = len(args) < 4 or not self._truth(self._eval(args[3], sheet))
            if not isinstance(table, list) or not table or not isinstance(table[0], list):
                raise FormulaError("VLOOKUP table must be a rectangular range")
            candidate = None
            for row in table:
                if row and row[0] == needle:
                    if column < 1 or column > len(row):
                        raise FormulaError("VLOOKUP column outside table")
                    return row[column - 1]
                if not exact and row and self._compare(row[0], needle, "<="):
                    candidate = row
            if candidate is not None:
                return candidate[column - 1]
            raise FormulaError(f"VLOOKUP could not find {needle!r}")
        if name == "XLOOKUP":
            if len(args) not in (3, 4):
                raise FormulaError("XLOOKUP expects three or four arguments")
            needles = self._eval(args[0], sheet)
            lookup = self._eval(args[1], sheet)
            returns = self._eval(args[2], sheet)
            if isinstance(needles, list) and len(needles) == 1 and isinstance(needles[0], list):
                needles = needles[0]
            if isinstance(lookup, list) and len(lookup) == 1 and isinstance(lookup[0], list):
                lookup = lookup[0]
            if isinstance(returns, list) and len(returns) == 1 and isinstance(returns[0], list):
                returns = returns[0]
            if not isinstance(lookup, list) or not isinstance(returns, list):
                raise FormulaError("XLOOKUP arrays must be ranges")
            needle_list = needles if isinstance(needles, list) else [needles]
            result = []
            for needle in needle_list:
                try:
                    index = lookup.index(needle)
                    result.append(returns[index])
                except (ValueError, IndexError):
                    if len(args) == 4:
                        result.append(self._eval(args[3], sheet))
                    else:
                        raise FormulaError(
                            f"XLOOKUP could not find {needle!r}"
                        ) from None
            return result if isinstance(needles, list) else result[0]
        raise FormulaError(f"unsupported function {name}")

    def _range(self, reference: str, current_sheet: str) -> Iterable[Any]:
        if "!" in reference:
            raw_sheet, coords = reference.rsplit("!", 1)
            sheet = raw_sheet.strip("'").replace("''", "'")
        else:
            sheet, coords = current_sheet, reference
        coords = coords.replace("$", "").upper()
        cache_key = f"{sheet}!{coords}"
        if cache_key in self.range_cache:
            return self.range_cache[cache_key]
        dimensions = self.sheets[sheet]["dimensions"]
        col_match = _COL_RANGE_RE.match(coords)
        if col_match:
            first_col, last_col = map(column_number, col_match.groups())
            first_row, last_row = 1, dimensions["max_row"]
        else:
            start, end = coords.split(":", 1)
            sm, em = _CELL_RE.match(start), _CELL_RE.match(end)
            if sm is None or em is None:
                raise FormulaError(f"invalid range {reference}")
            first_col, first_row = column_number(sm.group(1)), int(sm.group(2))
            last_col, last_row = column_number(em.group(1)), int(em.group(2))
        rows = []
        for row in range(first_row, last_row + 1):
            values = [self.value(f"{sheet}!{column_name(col)}{row}")
                      for col in range(first_col, last_col + 1)]
            rows.append(values)
        result = [row[0] for row in rows] if first_col == last_col else rows
        self.range_cache[cache_key] = result
        return result

    @staticmethod
    def _number(value: Any) -> float:
        if value in (None, "", False):
            return 0.0
        if value is True:
            return 1.0
        if isinstance(value, (int, float)):
            return float(value)
        try:
            return float(value)
        except (TypeError, ValueError) as exc:
            raise FormulaError(f"expected number, got {value!r}") from exc

    @staticmethod
    def _truth(value: Any) -> bool:
        return bool(value)

    @staticmethod
    def _compare(left: Any, right: Any, operator: str) -> bool:
        cmp_a: Any
        cmp_b: Any
        if isinstance(left, str) or isinstance(right, str):
            cmp_a, cmp_b = str(left).casefold(), str(right).casefold()
        else:
            cmp_a, cmp_b = left or 0, right or 0
        return {"=": cmp_a == cmp_b, "<>": cmp_a != cmp_b, "<": cmp_a < cmp_b, ">": cmp_a > cmp_b,
                "<=": cmp_a <= cmp_b, ">=": cmp_a >= cmp_b}[operator]
