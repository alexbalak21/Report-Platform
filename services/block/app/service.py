"""Block definition -> stored version: sanitize, scope the CSS, check the template syntax."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass, field
from typing import Any

from rp_shared import placeholders
from rp_shared.css_scope import scope_class, scope_css
from rp_shared.errors import ValidationFailed
from rp_shared.sanitizer import sanitize_template_html
from rp_shared.schemas.block import BlockDefinition

from .models import Block, BlockVersion

# A placeholder inside style="..." is mangled by the inline-style filter ("height:{ mm").
_STYLE_ATTR_PLACEHOLDER = re.compile(r"""style\s*=\s*(["'])[^"']*\{\{""", re.IGNORECASE)


@dataclass
class Built:
    """Everything derived from an author's definition, before it is stored."""

    definition: dict[str, Any]  # author fields + computed placeholders / loops
    html: str
    css: str
    placeholders: list[str]
    loops: list[str]
    loop_columns: list[str]
    warnings: list[str] = field(default_factory=list)


COMPUTED_FIELDS = {"placeholders", "loops", "loop_columns"}


def authored_fields(defn: BlockDefinition) -> dict[str, Any]:
    return defn.model_dump(mode="json")


def content_hash(defn: BlockDefinition) -> str:
    canonical = json.dumps(authored_fields(defn), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def _loop_keys(nodes: list) -> set[str]:
    keys: set[str] = set()
    for node in nodes:
        if isinstance(node, placeholders.Each):
            keys.add(node.key)
            keys |= _loop_keys(node.body)
        elif isinstance(node, placeholders.If):
            keys |= _loop_keys(node.then) | _loop_keys(node.otherwise)
    return keys


def _loop_columns(nodes: list, in_loop: bool = False) -> set[str]:
    """Keys read inside {{#each}} bodies (`this.x` counts as `x`): the data source columns."""
    cols: set[str] = set()
    for node in nodes:
        if isinstance(node, placeholders.Var) and in_loop:
            key = node.key.removeprefix("this.")
            if (
                key != "this"
                and key not in placeholders.BUILTIN_KEYS
                and not key.startswith("props")
            ):
                cols.add(key)
        elif isinstance(node, placeholders.Each):
            cols |= _loop_columns(node.body, True)
        elif isinstance(node, placeholders.If):
            if in_loop and node.key not in placeholders.BUILTIN_KEYS:
                cols.add(node.key.removeprefix("this."))
            cols |= _loop_columns(node.then, in_loop) | _loop_columns(node.otherwise, in_loop)
    return cols


def syntax_issues(html: str) -> list[dict[str, Any]]:
    return [
        {"path": "html", "message": i["message"], "line": i["line"], "col": i["col"]}
        for i in placeholders.validate(html)
    ]


def build(defn: BlockDefinition, scope: str) -> Built:
    """Sanitize and analyse a definition. Raises ValidationFailed on template syntax errors."""
    html = sanitize_template_html(defn.html)
    if issues := syntax_issues(html):
        raise ValidationFailed("The block HTML has template syntax errors", details=issues)
    scoped = scope_css(defn.css, scope)
    keys = placeholders.extract_keys(html)
    ast = placeholders.parse(html)
    loops = sorted(_loop_keys(ast))
    loop_columns = sorted(_loop_columns(ast))
    data_keys = sorted(k for k in keys if k != "props" and not k.startswith("props."))
    warnings = list(scoped.warnings)

    if _STYLE_ATTR_PLACEHOLDER.search(defn.html):
        warnings.append(
            "Placeholders inside a style attribute are not supported; "
            "use a CSS class and the stylesheet instead"
        )
    used_props = {k.split(".", 1)[1] for k in keys if k.startswith("props.")}
    for name in sorted(used_props - set(defn.props_schema)):
        warnings.append(
            f"'{{{{props.{name}}}}}' is used but 'props_schema' does not define '{name}'"
        )
    if loops and not any(p.type == "datasource" for p in defn.props_schema.values()):
        warnings.append(
            "The block loops over data but has no 'datasource' property to choose the table"
        )
    definition = {
        **authored_fields(defn),
        "placeholders": data_keys,
        "loops": loops,
        "loop_columns": loop_columns,
    }
    return Built(definition, html, scoped.css, data_keys, loops, loop_columns, warnings)


def add_version(
    session, block: Block, defn: BlockDefinition, user_id: uuid.UUID | None
) -> tuple[BlockVersion | None, Built]:
    """Store `defn` as the next version of `block`, or return (None, built) if nothing changed.

    `block.current_version` must be the latest stored version (0 for a block without any).
    """
    digest = content_hash(defn)
    if block.current_version:
        current = next((v for v in block.versions if v.version == block.current_version), None)
        if current is not None and current.content_hash == digest:
            return None, build(defn, current.scope_class)
    version = block.current_version + 1
    scope = scope_class(block.id, version)
    built = build(defn, scope)
    row = BlockVersion(
        block_id=block.id,
        version=version,
        definition=built.definition,
        html_sanitized=built.html,
        css_sanitized=built.css,
        scope_class=scope,
        content_hash=digest,
        created_by=user_id,
    )
    block.versions.append(row)
    block.current_version = version
    return row, built
