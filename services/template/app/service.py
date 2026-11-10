"""Template business rules: hashing, usage tracking and validation.

Validation answers "can this document be published and rendered?" and is also what the builder
shows as a checklist. Issues are {severity: error|warning, code, message, ...}; only errors
block publishing.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

from rp_shared import placeholders
from rp_shared.schemas.template_document import TemplateDocument
from rp_shared.template_render import (  # noqa: F401  (collect_pins / datasources re-exported)
    BlockSnapshot,
    Pins,
    collect_pins,
    datasource_table,
    datasources,
    pin_key,
    prop_values,
)
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .clients import BlockDirectory, Catalog
from .models import Template, TemplateBlockUsage

MIN_BODY_HEIGHT_MM = 30.0


def content_hash(doc: TemplateDocument) -> str:
    canonical = json.dumps(doc.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Usage tracking
# ---------------------------------------------------------------------------
def sync_usage(session: Session, template: Template, source: str, doc: TemplateDocument) -> None:
    """Replace the (template, source) usage rows with what `doc` pins now."""
    session.execute(
        delete(TemplateBlockUsage).where(
            TemplateBlockUsage.template_id == template.id, TemplateBlockUsage.source == source
        )
    )
    for block_id, version in doc.block_pins().items():
        session.add(
            TemplateBlockUsage(
                template_id=template.id, source=source, block_id=block_id, block_version=version
            )
        )


def prune_outdated(template: Template, doc: TemplateDocument) -> None:
    """Forget flagged blocks the draft no longer pins at an older version."""
    pins = {str(k): v for k, v in doc.block_pins().items()}
    template.outdated_blocks = [
        e for e in (template.outdated_blocks or []) if pins.get(e["block_id"], 10**9) < e["latest"]
    ]


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
def _issue(severity: str, code: str, message: str, **extra: Any) -> dict[str, Any]:
    return {"severity": severity, "code": code, "message": message, **extra}


def region_allows(region: str, block_region: str) -> bool:
    """`region` is header | body | footer | overlay; blocks declare where they may go."""
    if block_region == "any":
        return True
    return block_region == ("body" if region == "overlay" else region)


def _prop_problem(spec: dict[str, Any], value: Any) -> str | None:
    kind = spec.get("type", "string")
    if value is None:
        return None
    if kind == "number":
        return None if isinstance(value, (int, float)) and not isinstance(value, bool) else "number"
    if kind == "boolean":
        return None if isinstance(value, bool) else "true or false"
    if kind == "select":
        options = spec.get("options", [])
        return None if value in options else "one of: " + ", ".join(options)
    return None if isinstance(value, str) else "text"


def _check_instance(
    region: str,
    inst,
    snap: BlockSnapshot,
    catalog: Catalog | None,
    summary: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    where = {"instance_id": inst.instance_id, "block_name": snap.name}
    definition = snap.definition

    block_region = definition.get("repeatable_region", "body")
    if not region_allows(region, block_region):
        out.append(
            _issue(
                "error",
                "REGION_NOT_ALLOWED",
                f"'{snap.name}' can only be placed in the {block_region} "
                f"(not in the {region if region != 'overlay' else 'free layer'})",
                **where,
            )
        )

    if summary is not None:
        if summary.get("is_archived"):
            out.append(_issue("warning", "BLOCK_ARCHIVED", f"'{snap.name}' is archived", **where))
        latest = summary.get("current_version", inst.block_version)
        if latest > inst.block_version:
            out.append(
                _issue(
                    "warning",
                    "BLOCK_OUTDATED",
                    f"'{snap.name}' has a newer version (v{inst.block_version} -> v{latest})",
                    pinned=inst.block_version,
                    latest=latest,
                    **where,
                )
            )

    schema = definition.get("props_schema") or {}
    values = prop_values(snap, inst)
    for name, spec in schema.items():
        label = spec.get("label") or name
        value = values.get(name)
        if spec.get("required") and value in (None, ""):
            out.append(
                _issue(
                    "error",
                    "PROP_REQUIRED",
                    f"'{snap.name}': '{label}' is required",
                    prop=name,
                    **where,
                )
            )
        elif (expected := _prop_problem(spec, inst.props.get(name))) is not None:
            out.append(
                _issue(
                    "error",
                    "PROP_INVALID",
                    f"'{snap.name}': '{label}' must be {expected}",
                    prop=name,
                    **where,
                )
            )
    for name in sorted(set(inst.props) - set(schema)):
        out.append(
            _issue(
                "warning",
                "PROP_UNKNOWN",
                f"'{snap.name}' has no setting '{name}' (ignored)",
                prop=name,
                **where,
            )
        )

    loops = definition.get("loops") or []
    if loops:
        has_ds_prop = any(s.get("type") == "datasource" for s in schema.values())
        table = datasource_table(snap, values)
        if not has_ds_prop:
            out.append(
                _issue(
                    "warning",
                    "NO_DATASOURCE_PROP",
                    f"'{snap.name}' loops over data but has no data source setting",
                    **where,
                )
            )
        elif table is None:
            out.append(
                _issue(
                    "error",
                    "DATASOURCE_REQUIRED",
                    f"'{snap.name}': choose the data table",
                    **where,
                )
            )
        elif catalog is not None and not catalog.empty:
            if table not in catalog.tables:
                out.append(
                    _issue(
                        "error",
                        "DATASOURCE_UNKNOWN",
                        f"'{snap.name}': table '{table}' does not exist",
                        **where,
                    )
                )
            else:
                for col in definition.get("loop_columns") or []:
                    if col not in catalog.tables[table]:
                        out.append(
                            _issue(
                                "error",
                                "UNKNOWN_KEY",
                                f"'{snap.name}': column '{col}' does not exist in '{table}'",
                                key=col,
                                **where,
                            )
                        )

    if catalog is not None and not catalog.empty:
        known = catalog.keys | catalog.builtins | placeholders.BUILTIN_KEYS
        skip = set(loops)
        for key in definition.get("placeholders") or []:
            if key in skip or key in known:
                continue
            out.append(
                _issue(
                    "error",
                    "UNKNOWN_KEY",
                    f"'{snap.name}': unknown keyword '{key}'",
                    key=key,
                    **where,
                )
            )
    return out


def validate_document(
    doc: TemplateDocument, blocks: BlockDirectory, catalog: Catalog | None
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    summaries: dict[str, dict[str, Any] | None] = {}

    for region, inst in doc.instances_by_region():
        snap = blocks.snapshot(inst.block_id, inst.block_version)
        if snap is None:
            issues.append(
                _issue(
                    "error",
                    "BLOCK_NOT_FOUND",
                    f"Block {inst.block_id} version {inst.block_version} does not exist",
                    instance_id=inst.instance_id,
                )
            )
            continue
        bid = str(inst.block_id)
        if bid not in summaries:
            summaries[bid] = blocks.summary(bid)
        issues.extend(_check_instance(region, inst, snap, catalog, summaries[bid]))

    page, m, r = doc.page, doc.page.margins_mm, doc.regions
    used = (r.header.height_mm or 0) if r.header.items else 0
    used += (r.footer.height_mm or 0) if r.footer.items else 0
    room = page.height_mm - m.t - m.b - used
    if room < MIN_BODY_HEIGHT_MM:
        issues.append(
            _issue(
                "error",
                "NO_BODY_ROOM",
                f"Margins, header and footer leave only {max(room, 0):g} mm for the body "
                f"(at least {MIN_BODY_HEIGHT_MM:g} mm needed)",
            )
        )
    for o in doc.overlays:
        if o.x_mm + o.w_mm > page.width_mm or o.y_mm + o.h_mm > page.height_mm:
            issues.append(
                _issue(
                    "warning",
                    "OVERLAY_OUTSIDE_PAGE",
                    "A free block extends beyond the page",
                    instance_id=o.instance_id,
                )
            )
    if not r.body.items and not doc.overlays:
        issues.append(_issue("warning", "EMPTY_BODY", "The page body is empty"))
    if catalog is None:
        issues.append(
            _issue(
                "warning",
                "CATALOG_UNAVAILABLE",
                "The data service could not be reached: keywords were not checked",
            )
        )
    elif catalog.empty and doc.instances():
        issues.append(
            _issue(
                "warning",
                "CATALOG_EMPTY",
                "No data has been imported yet: keywords were not checked",
            )
        )

    issues.sort(key=lambda i: 0 if i["severity"] == "error" else 1)
    return issues


def summarize(issues: list[dict[str, Any]]) -> dict[str, Any]:
    errors = sum(1 for i in issues if i["severity"] == "error")
    return {
        "ok": errors == 0,
        "errors": errors,
        "warnings": len(issues) - errors,
        "issues": issues,
    }


def latest_pins(session: Session, template_id: uuid.UUID, source: str) -> dict[uuid.UUID, int]:
    rows = session.execute(
        select(TemplateBlockUsage.block_id, TemplateBlockUsage.block_version).where(
            TemplateBlockUsage.template_id == template_id, TemplateBlockUsage.source == source
        )
    ).all()
    out: dict[uuid.UUID, int] = {}
    for block_id, version in rows:
        out[block_id] = max(out.get(block_id, 0), version)
    return out
