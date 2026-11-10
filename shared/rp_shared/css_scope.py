"""Scope user-authored block CSS so one block can never restyle the page around it.

Every selector is prefixed with the block's wrapper class (see `scope_class`). Anything that
could reach outside the block or load remote content is dropped and reported as a warning:
@import, @font-face, @page, @keyframes, remote url(), `position: fixed|sticky`, IE-era
`expression()` / `behavior`. The output is safe to embed in a <style> element.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field

import tinycss2
from tinycss2 import ast

PREVIEW_SCOPE = "blk-preview"

# At-rules that only carry conditions + nested rules, so they can be scoped recursively.
_CONDITIONAL_AT_RULES = {"media", "supports"}

# Declarations that are dropped whatever their value.
_FORBIDDEN_PROPERTIES = {"behavior", "-moz-binding", "-ms-behavior"}
# Functions that fetch or execute something; dropped wherever they appear in a value.
_FORBIDDEN_FUNCTIONS = {"expression", "image", "image-set", "src", "element", "cross-fade", "paint"}
_ALLOWED_DATA_URL = re.compile(r"^data:image/(png|jpe?g|gif|webp|svg\+xml)[;,]", re.IGNORECASE)
_ROOT_SELECTOR = re.compile(r"^(html|body|:root)(?![\w-])", re.IGNORECASE)


def scope_class(block_id: uuid.UUID | str, version: int | None = None) -> str:
    """CSS class on the wrapper of one block version: `blk-<id hex>-v<version>`.

    The version is part of the class so two versions of the same block can sit in one
    template without their stylesheets colliding.
    """
    base = f"blk-{str(block_id).replace('-', '')}"
    return base if version is None else f"{base}-v{version}"


@dataclass
class ScopedCss:
    css: str
    warnings: list[str] = field(default_factory=list)


def scope_css(css: str, scope: str) -> ScopedCss:
    """Return `css` with every selector confined to `.<scope>` and unsafe parts removed."""
    warnings: list[str] = []
    rules = tinycss2.parse_stylesheet(css or "", skip_comments=True, skip_whitespace=True)
    out = _scope_rules(rules, f".{scope}", warnings)
    # `<` can only appear inside strings in valid CSS; escaping it makes "</style>" inert.
    return ScopedCss("\n".join(out).replace("<", "\\3c "), warnings)


def _scope_rules(rules: list, prefix: str, warnings: list[str]) -> list[str]:
    out: list[str] = []
    for rule in rules:
        if isinstance(rule, ast.ParseError):
            warnings.append(f"CSS syntax error ignored: {rule.message}")
        elif isinstance(rule, ast.AtRule):
            text = _scope_at_rule(rule, prefix, warnings)
            if text:
                out.append(text)
        elif isinstance(rule, ast.QualifiedRule):
            text = _scope_qualified_rule(rule, prefix, warnings)
            if text:
                out.append(text)
    return out


def _scope_at_rule(rule: ast.AtRule, prefix: str, warnings: list[str]) -> str | None:
    name = rule.lower_at_keyword
    if name in _CONDITIONAL_AT_RULES and rule.content is not None:
        inner = tinycss2.parse_rule_list(rule.content, skip_comments=True, skip_whitespace=True)
        body = _scope_rules(inner, prefix, warnings)
        if not body:
            return None
        prelude = tinycss2.serialize(rule.prelude).strip()
        return f"@{name} {prelude} {{\n" + "\n".join(body) + "\n}"
    warnings.append(f"@{name} is not allowed in blocks and was removed")
    return None


def _split_selectors(prelude: list) -> list[str]:
    parts: list[list] = [[]]
    for token in prelude:
        if isinstance(token, ast.LiteralToken) and token.value == ",":
            parts.append([])
        else:
            parts[-1].append(token)
    return [s for s in (tinycss2.serialize(p).strip() for p in parts) if s]


def _scope_selector(selector: str, prefix: str) -> str:
    m = _ROOT_SELECTOR.match(selector)
    if m:  # `body td` in a block means "td inside this block"
        rest = selector[m.end() :].strip()
        return f"{prefix} {rest}" if rest else prefix
    return f"{prefix} {selector}"


def _scope_qualified_rule(rule: ast.QualifiedRule, prefix: str, warnings: list[str]) -> str | None:
    selectors = _split_selectors(rule.prelude)
    if not selectors:
        return None
    declarations = tinycss2.parse_blocks_contents(
        rule.content, skip_comments=True, skip_whitespace=True
    )
    lines: list[str] = []
    for item in declarations:
        if isinstance(item, ast.ParseError):
            warnings.append(f"CSS syntax error ignored: {item.message}")
        elif isinstance(item, ast.Declaration):
            line = _declaration(item, warnings)
            if line:
                lines.append(line)
        else:
            warnings.append("Nested CSS rules are not supported and were removed")
    if not lines:
        return None
    scoped = ",\n".join(_scope_selector(s, prefix) for s in selectors)
    return f"{scoped} {{ {' '.join(lines)} }}"


def _declaration(decl: ast.Declaration, warnings: list[str]) -> str | None:
    name = decl.lower_name
    value = tinycss2.serialize(decl.value).strip()
    if name in _FORBIDDEN_PROPERTIES:
        warnings.append(f"'{name}' is not allowed and was removed")
        return None
    if name == "position" and value.lower() in {"fixed", "sticky"}:
        warnings.append(f"'position: {value}' is not allowed and was removed")
        return None
    problem = _unsafe_value(decl.value)
    if problem:
        warnings.append(f"'{name}' was removed: {problem}")
        return None
    important = " !important" if decl.important else ""
    return f"{decl.name}: {value}{important};"


def _unsafe_value(tokens: list) -> str | None:
    """Reason a value must be dropped, or None when it is fine."""
    for token in tokens:
        if isinstance(token, ast.URLToken):
            if not _url_allowed(token.value):
                return f"external url({token.value[:40]}) is not allowed"
        elif isinstance(token, ast.FunctionBlock):
            if token.lower_name in _FORBIDDEN_FUNCTIONS:
                return f"{token.lower_name}() is not allowed"
            if token.lower_name == "url":
                arg = next((a for a in token.arguments if isinstance(a, ast.StringToken)), None)
                if arg is None or not _url_allowed(arg.value):
                    return "external url() is not allowed"
            nested = _unsafe_value(token.arguments)
            if nested:
                return nested
    return None


def _url_allowed(url: str) -> bool:
    url = url.strip()
    return bool(_ALLOWED_DATA_URL.match(url)) or url.startswith("/api/") or url.startswith("#")
