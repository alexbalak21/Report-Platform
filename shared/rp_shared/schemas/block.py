"""Block definition: the contract between the Block, Template and Report services.

Block HTML uses the placeholder grammar (ADR-003). Values set on a block instance in a template
are reachable as `{{props.<name>}}`; the data columns of the bound table as `{{<column>}}`.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from ..errors import ValidationFailed

PROP_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
CATEGORIES = ("header", "footer", "text", "table", "data", "media", "layout", "other")
MAX_HTML = 200_000
MAX_CSS = 100_000

PropType = Literal["string", "number", "boolean", "select", "datasource"]
Region = Literal["header", "footer", "body", "any"]


class PropSpec(BaseModel):
    """One setting the template designer can change on a block instance."""

    model_config = ConfigDict(extra="forbid")

    type: PropType = "string"
    label: str | None = Field(default=None, max_length=80)
    default: str | int | float | bool | None = None
    options: list[str] = Field(default_factory=list, max_length=50)
    required: bool = False

    @model_validator(mode="after")
    def _consistent(self) -> PropSpec:
        if self.type == "select":
            if not self.options:
                raise ValueError("a 'select' property needs at least one option")
            if self.default is not None and self.default not in self.options:
                raise ValueError("the default must be one of the options")
        elif self.options:
            raise ValueError("'options' only applies to 'select' properties")
        if self.default is not None:
            if self.type == "number" and (
                isinstance(self.default, bool) or not isinstance(self.default, (int, float))
            ):
                raise ValueError("the default of a 'number' property must be a number")
            if self.type == "boolean" and not isinstance(self.default, bool):
                raise ValueError("the default of a 'boolean' property must be true or false")
            if self.type in {"string", "datasource"} and not isinstance(self.default, str):
                raise ValueError(f"the default of a '{self.type}' property must be text")
        return self


class SizeMm(BaseModel):
    model_config = ConfigDict(extra="forbid")

    w: float = Field(gt=0, le=210)
    h: float = Field(gt=0, le=297)


class BlockDefinition(BaseModel):
    # Unknown keys (e.g. the computed `placeholders` of an exported block) are ignored, not kept.
    model_config = ConfigDict(extra="ignore")

    html: str = Field(min_length=1, max_length=MAX_HTML)
    css: str = Field(default="", max_length=MAX_CSS)
    props_schema: dict[str, PropSpec] = Field(default_factory=dict, max_length=40)
    default_size_mm: SizeMm = SizeMm(w=180, h=20)
    resizable: bool = True
    repeatable_region: Region = "body"

    @field_validator("props_schema")
    @classmethod
    def _prop_names(cls, value: dict[str, PropSpec]) -> dict[str, PropSpec]:
        for name in value:
            if not PROP_NAME_RE.match(name):
                raise ValueError(
                    f"property name '{name}' must be lowercase letters, digits and underscores"
                )
        return value


def parse_block_definition(raw: Any) -> BlockDefinition:
    """Validate a definition; raise ValidationFailed with one detail per problem."""
    try:
        return BlockDefinition.model_validate(raw)
    except ValidationError as exc:
        details = [
            {
                "path": ".".join(str(p) for p in err["loc"]),
                "message": err["msg"].removeprefix("Value error, "),
            }
            for err in exc.errors()
        ]
        raise ValidationFailed("Invalid block definition", details=details) from exc
