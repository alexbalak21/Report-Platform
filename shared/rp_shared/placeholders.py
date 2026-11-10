"""Placeholder grammar: parser, validator and renderer (single source of truth).

Syntax
    {{ key }}                          value
    {{ key | op1 | op2:arg }}          operation chain, applied left to right
    {{ #each rows }} ... {{ /each }}   loop over a list ({{ col }} or {{ this.col }} inside)
    {{ #if key }} ... {{ else }} ... {{ /if }}

Rules carried over from the desktop app's bugs:
  * CRLF is normalised to LF before parsing.
  * Operations run strictly left to right (multiply -> round -> suffix).
  * Rounding is half-up like Excel, not Python's banker's rounding.
  * A key missing from the context is *unresolved* (reported), never silently blank.
    A key that is present but empty (None) renders empty.
  * Values are HTML-escaped unless the chain contains `raw`.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

TAG_RE = re.compile(r"\{\{(.*?)\}\}", re.DOTALL)
KEY_RE = re.compile(r"^[a-z_][a-z0-9_]*(\.[a-z0-9_]+)*$")
OP_NAME_RE = re.compile(r"^[a-z_][a-z0-9_]*$")
KNOWN_OPS = {
    "date",
    "upper",
    "lower",
    "multiply",
    "round",
    "suffix",
    "prefix",
    "default",
    "number",
    "raw",
}
OPS_WITH_REQUIRED_ARG = {"date", "multiply", "round", "suffix", "prefix", "default"}


# ---------------------------------------------------------------------------
# AST
# ---------------------------------------------------------------------------
@dataclass
class Op:
    name: str
    arg: str | None = None


@dataclass
class Text:
    value: str


@dataclass
class Var:
    key: str
    ops: list[Op] = field(default_factory=list)
    line: int = 1
    col: int = 1


@dataclass
class Each:
    key: str
    body: list = field(default_factory=list)


@dataclass
class If:
    key: str
    then: list = field(default_factory=list)
    otherwise: list = field(default_factory=list)


class TemplateSyntaxError(Exception):
    def __init__(self, message: str, line: int, col: int) -> None:
        super().__init__(f"{message} (line {line}, col {col})")
        self.message = message
        self.line = line
        self.col = col


def normalize(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _pos(text: str, offset: int) -> tuple[int, int]:
    line = text.count("\n", 0, offset) + 1
    col = offset - (text.rfind("\n", 0, offset) + 1) + 1
    return line, col


def _parse_ops(parts: list[str], text: str, offset: int) -> list[Op]:
    ops: list[Op] = []
    for raw in parts:
        raw = raw.strip()
        name, sep, arg = raw.partition(":")  # first colon only: date:%H:%M stays intact
        name = name.strip()
        line, col = _pos(text, offset)
        if not OP_NAME_RE.match(name) or name not in KNOWN_OPS:
            raise TemplateSyntaxError(f"Unknown operation '{name}'", line, col)
        if name in OPS_WITH_REQUIRED_ARG and not sep:
            raise TemplateSyntaxError(f"Operation '{name}' needs an argument", line, col)
        ops.append(Op(name, arg if sep else None))
    return ops


def parse(text: str) -> list:
    """Parse text into an AST. Raises TemplateSyntaxError with line/col."""
    text = normalize(text)
    root: list = []
    # stack of (container_list, node_or_None, kind)
    stack: list[tuple[list, Any, str]] = [(root, None, "root")]
    pos = 0

    for m in TAG_RE.finditer(text):
        if m.start() > pos:
            stack[-1][0].append(Text(text[pos : m.start()]))
        pos = m.end()
        line, col = _pos(text, m.start())
        body = m.group(1).strip()
        current = stack[-1][0]

        if body.startswith("#each") or body.startswith("#if"):
            kind, _, key = body[1:].partition(" ")
            key = key.strip()
            if not KEY_RE.match(key):
                raise TemplateSyntaxError(f"Invalid key '{key}' in #{kind}", line, col)
            node = Each(key) if kind == "each" else If(key)
            current.append(node)
            stack.append((node.body if kind == "each" else node.then, node, kind))
        elif body == "else":
            if stack[-1][2] != "if":
                raise TemplateSyntaxError("'else' outside of #if", line, col)
            node = stack[-1][1]
            stack[-1] = (node.otherwise, node, "if")
        elif body in ("/each", "/if"):
            expected = body[1:]
            if stack[-1][2] != expected:
                raise TemplateSyntaxError(f"Unexpected {{{{{body}}}}}", line, col)
            stack.pop()
        else:
            key, *op_parts = body.split("|")
            key = key.strip()
            if not KEY_RE.match(key):
                raise TemplateSyntaxError(f"Invalid keyword '{key}'", line, col)
            current.append(Var(key, _parse_ops(op_parts, text, m.start()), line, col))

    if len(stack) > 1:
        kind = stack[-1][2]
        line, col = _pos(text, len(text))
        raise TemplateSyntaxError(f"Unclosed #{kind}", line, col)
    if pos < len(text):
        root.append(Text(text[pos:]))
    return root


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------
def extract_keys(text: str) -> set[str]:
    """Top-level keywords a template needs from the context (loop-scoped keys excluded)."""
    keys: set[str] = set()

    def walk(nodes: list, in_loop: bool) -> None:
        for n in nodes:
            if isinstance(n, Var):
                if not in_loop and n.key not in BUILTIN_KEYS and not n.key.startswith("this"):
                    keys.add(n.key)
            elif isinstance(n, Each):
                if not in_loop:
                    keys.add(n.key)
                walk(n.body, True)
            elif isinstance(n, If):
                if not in_loop:
                    keys.add(n.key)
                walk(n.then, in_loop)
                walk(n.otherwise, in_loop)

    walk(parse(text), False)
    return keys


BUILTIN_KEYS = {"today", "now", "report_number", "page", "pages"}


def validate(text: str, known_keys: set[str] | None = None) -> list[dict[str, Any]]:
    """Return a list of issues: [{code, message, key?, line, col}]. Empty list = valid."""
    try:
        ast = parse(text)
    except TemplateSyntaxError as exc:
        return [{"code": "SYNTAX", "message": exc.message, "line": exc.line, "col": exc.col}]
    issues: list[dict[str, Any]] = []
    if known_keys is not None:
        allowed = known_keys | BUILTIN_KEYS

        def walk(nodes: list, in_loop: bool) -> None:
            for n in nodes:
                if isinstance(n, Var) and not in_loop:
                    root = n.key.split(".")[0]
                    if n.key not in allowed and root not in allowed and root != "this":
                        issues.append(
                            {
                                "code": "UNKNOWN_KEY",
                                "message": f"Unknown keyword '{n.key}'",
                                "key": n.key,
                                "line": n.line,
                                "col": n.col,
                            }
                        )
                elif isinstance(n, Each):
                    walk(n.body, True)
                elif isinstance(n, If):
                    walk(n.then, in_loop)
                    walk(n.otherwise, in_loop)

        walk(ast, False)
    return issues


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------
_MISSING = object()


def builtins() -> dict[str, Any]:
    now = datetime.now()
    return {"today": now.date(), "now": now}


def _lookup(ctx: dict[str, Any], key: str) -> Any:
    cur: Any = ctx
    for part in key.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return _MISSING
    return cur


def _to_decimal(value: Any) -> Decimal:
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool):
        raise ValueError("boolean is not a number")
    try:
        return Decimal(str(value).strip().replace(",", ".").replace(" ", "").replace(" ", ""))
    except InvalidOperation as exc:
        raise ValueError(f"'{value}' is not a number") from exc


def _fmt_decimal(d: Decimal) -> str:
    s = format(d, "f")
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s or "0"


def _to_date(value: Any) -> date | datetime:
    if isinstance(value, (date, datetime)):
        return value
    s = str(value).strip()
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%d/%m/%Y"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    raise ValueError(f"'{value}' is not a date")


def apply_op(value: Any, op: Op) -> Any:
    n = op.name
    if n == "default":
        return op.arg if value in (None, "") else value
    if value is None:
        return value
    if n == "upper":
        return str(value).upper()
    if n == "lower":
        return str(value).lower()
    if n == "prefix":
        return f"{op.arg}{_fmt_decimal(value) if isinstance(value, Decimal) else value}"
    if n == "suffix":
        return f"{_fmt_decimal(value) if isinstance(value, Decimal) else value}{op.arg}"
    if n == "date":
        return _to_date(value).strftime(op.arg or "%d/%m/%Y")
    if n == "multiply":
        # normalise trailing zeros (12.600 -> 12.6); `round` is what fixes decimal places
        return Decimal(_fmt_decimal(_to_decimal(value) * _to_decimal(op.arg)))
    if n == "round":
        places = int(op.arg or 0)
        quant = Decimal(1).scaleb(-places)
        return _to_decimal(value).quantize(quant, rounding=ROUND_HALF_UP)
    if n == "number":
        d = _to_decimal(value)
        exp = d.as_tuple().exponent
        decimals = max(0, -exp) if isinstance(exp, int) else 0
        s = f"{d:,.{decimals}f}"
        return s.replace(",", " ").replace(".", ",")  # French style: 1 234,5
    if n == "raw":
        return value
    raise ValueError(f"Unknown operation '{n}'")


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, Decimal):
        return _fmt_decimal(value) if "." not in format(value, "f") else format(value, "f")
    if isinstance(value, datetime):
        return value.strftime("%d/%m/%Y")
    if isinstance(value, date):
        return value.strftime("%d/%m/%Y")
    if isinstance(value, float):
        return _fmt_decimal(Decimal(str(value)))
    return str(value)


def _truthy(value: Any) -> bool:
    if value is _MISSING or value is None:
        return False
    if isinstance(value, str):
        return value.strip() != ""
    if isinstance(value, (list, dict, tuple, set)):
        return len(value) > 0
    if isinstance(value, Decimal):
        return value != 0
    return bool(value)


@dataclass
class RenderResult:
    text: str
    unresolved: list[str]
    errors: list[dict[str, Any]]

    @property
    def ok(self) -> bool:
        return not self.unresolved and not self.errors


def render(
    text: str,
    context: dict[str, Any],
    *,
    missing: str = "marker",
    escape: bool = True,
    with_builtins: bool = True,
) -> RenderResult:
    """Render `text` with `context`.

    missing: "marker" -> [[key?]], "blank" -> "", "error" -> raise KeyError
    Unresolved keys and per-value operation errors are always collected.
    """
    ast = parse(text)
    ctx = {**builtins(), **context} if with_builtins else dict(context)
    unresolved: list[str] = []
    errors: list[dict[str, Any]] = []

    def eval_var(node: Var, scope: dict[str, Any]) -> str:
        value = _lookup(scope, node.key)
        has_default = any(o.name == "default" for o in node.ops)
        if value is _MISSING and not has_default:
            if node.key not in unresolved:
                unresolved.append(node.key)
            if missing == "error":
                raise KeyError(node.key)
            return "" if missing == "blank" else f"[[{node.key}?]]"
        if value is _MISSING:
            value = None
        is_raw = False
        try:
            for op in node.ops:
                if op.name == "raw":
                    is_raw = True
                    continue
                value = apply_op(value, op)
        except (ValueError, InvalidOperation) as exc:
            errors.append({"key": node.key, "line": node.line, "col": node.col, "error": str(exc)})
            return f"[[{node.key}!]]"
        out = _stringify(value)
        return out if (is_raw or not escape) else html.escape(out, quote=True)

    def run(nodes: list, scope: dict[str, Any]) -> str:
        out: list[str] = []
        for n in nodes:
            if isinstance(n, Text):
                out.append(n.value)
            elif isinstance(n, Var):
                out.append(eval_var(n, scope))
            elif isinstance(n, If):
                cond = _lookup(scope, n.key)
                out.append(run(n.then if _truthy(cond) else n.otherwise, scope))
            elif isinstance(n, Each):
                rows = _lookup(scope, n.key)
                if rows is _MISSING:
                    if n.key not in unresolved:
                        unresolved.append(n.key)
                    if missing == "error":
                        raise KeyError(n.key)
                    continue
                for row in rows or []:
                    child = {**scope, "this": row}
                    if isinstance(row, dict):
                        child.update(row)
                    out.append(run(n.body, child))
        return "".join(out)

    return RenderResult(run(ast, ctx), unresolved, errors)
