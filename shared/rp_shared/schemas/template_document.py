"""TemplateDocument: the contract between the Template and Report services (and the builder UI).

A template is a structured document, not HTML. Block instances pin a block *version*, so editing
a block never silently changes a stored template.

Layout
    header / footer   fixed regions repeated on every printed page (flow, a fixed height_mm)
    body              block instances stacked top to bottom (flow layout: tables may grow)
    overlays          free blocks placed in mm on the first page (logos, stamps, signatures)
"""

from __future__ import annotations

import re
import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from ..errors import ValidationFailed

INSTANCE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
PropValue = str | int | float | bool | None

A4_W_MM = 210.0
A4_H_MM = 297.0
MAX_REGION_ITEMS = 100
MAX_OVERLAYS = 50
MAX_PROP_LEN = 2000


class Margins(BaseModel):
    model_config = ConfigDict(extra="forbid")

    t: float = Field(default=20, ge=0, le=60)
    r: float = Field(default=15, ge=0, le=60)
    b: float = Field(default=20, ge=0, le=60)
    l: float = Field(default=15, ge=0, le=60)  # noqa: E741  (top/right/bottom/left)


class Page(BaseModel):
    model_config = ConfigDict(extra="forbid")

    size: Literal["A4"] = "A4"
    orientation: Literal["portrait", "landscape"] = "portrait"
    margins_mm: Margins = Margins()

    @property
    def width_mm(self) -> float:
        return A4_W_MM if self.orientation == "portrait" else A4_H_MM

    @property
    def height_mm(self) -> float:
        return A4_H_MM if self.orientation == "portrait" else A4_W_MM


class BlockInstance(BaseModel):
    """One block placed on the page."""

    model_config = ConfigDict(extra="forbid")

    instance_id: str
    block_id: uuid.UUID
    block_version: int = Field(ge=1)
    locked: bool = False
    # Values for the block's `props_schema`; reachable in its HTML as {{props.<name>}}.
    props: dict[str, PropValue] = Field(default_factory=dict, max_length=40)

    @model_validator(mode="after")
    def _check(self) -> BlockInstance:
        if not INSTANCE_ID_RE.match(self.instance_id):
            raise ValueError("instance_id must be 1-64 letters, digits, '-' or '_'")
        for name, value in self.props.items():
            if isinstance(value, str) and len(value) > MAX_PROP_LEN:
                raise ValueError(f"property '{name}' is longer than {MAX_PROP_LEN} characters")
        return self


class Overlay(BlockInstance):
    x_mm: float = Field(ge=0, le=A4_H_MM)
    y_mm: float = Field(ge=0, le=A4_H_MM)
    w_mm: float = Field(gt=0, le=A4_H_MM)
    h_mm: float = Field(gt=0, le=A4_H_MM)
    z: int = Field(default=1, ge=0, le=1000)


class Region(BaseModel):
    model_config = ConfigDict(extra="forbid")

    height_mm: float | None = Field(default=None, gt=0, le=100)
    items: list[BlockInstance] = Field(default_factory=list, max_length=MAX_REGION_ITEMS)


class Regions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    header: Region = Region(height_mm=25)
    body: Region = Region()
    footer: Region = Region(height_mm=15)


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # False renders {{page}} and {{pages}} as empty text.
    page_numbering: bool = True
    # What the report generator does with a keyword that has no value (see ADR-003).
    missing_policy: Literal["error", "blank", "marker"] = "marker"


class TemplateDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    page: Page = Page()
    regions: Regions = Regions()
    overlays: list[Overlay] = Field(default_factory=list, max_length=MAX_OVERLAYS)
    settings: Settings = Settings()

    @model_validator(mode="after")
    def _unique_instances(self) -> TemplateDocument:
        seen: set[str] = set()
        for inst in self.instances():
            if inst.instance_id in seen:
                raise ValueError(f"instance_id '{inst.instance_id}' is used twice")
            seen.add(inst.instance_id)
        return self

    def instances(self) -> list[BlockInstance]:
        r = self.regions
        return [*r.header.items, *r.body.items, *r.footer.items, *self.overlays]

    def instances_by_region(self) -> list[tuple[str, BlockInstance]]:
        r = self.regions
        return [
            *(("header", i) for i in r.header.items),
            *(("body", i) for i in r.body.items),
            *(("footer", i) for i in r.footer.items),
            *(("overlay", i) for i in self.overlays),
        ]

    def block_pins(self) -> dict[uuid.UUID, int]:
        """block_id -> the highest version pinned by any instance (used for usage tracking)."""
        pins: dict[uuid.UUID, int] = {}
        for inst in self.instances():
            pins[inst.block_id] = max(pins.get(inst.block_id, 0), inst.block_version)
        return pins


def empty_document() -> dict[str, Any]:
    return TemplateDocument().model_dump(mode="json")


def parse_template_document(raw: Any) -> TemplateDocument:
    """Validate a document; raise ValidationFailed with one detail per problem."""
    try:
        return TemplateDocument.model_validate(raw)
    except ValidationError as exc:
        details = [
            {
                "path": ".".join(str(p) for p in err["loc"]),
                "message": err["msg"].removeprefix("Value error, "),
            }
            for err in exc.errors()
        ]
        raise ValidationFailed("Invalid template document", details=details) from exc
