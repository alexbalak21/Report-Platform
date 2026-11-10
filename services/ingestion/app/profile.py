"""Import profile: how a worksheet's columns map to database tables.

Strict by design (unknown keys are rejected, so a typo can't silently drop a mapping).
Same idea as the desktop app's mapping JSON, with explicit target tables/columns/types.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from rp_shared.errors import ValidationFailed

from .excel import norm_header

IDENT_RE = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
# Names the ingestion schema itself uses (matters on SQLite where there is no `data` schema).
RESERVED_TABLES = {
    "import_profiles",
    "uploads",
    "import_runs",
    "data_tables",
    "data_columns",
    "alembic_version",
}
ColumnType = Literal["string", "number", "integer", "date", "datetime", "boolean"]
NUMERIC_TYPES = {"number", "integer"}
NUMERIC_OPS = {"multiply", "round"}
STRING_OPS = {"uppercase", "lowercase", "strip", "prefix", "suffix"}


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Operation(_Strict):
    type: Literal["multiply", "round", "uppercase", "lowercase", "strip", "prefix", "suffix"]
    value: str | int | float | None = None
    decimals: int | None = Field(default=None, ge=0, le=10)

    @model_validator(mode="after")
    def _check(self) -> Operation:
        if self.type == "multiply" and (
            isinstance(self.value, bool) or not isinstance(self.value, (int, float, str))
        ):
            raise ValueError("'multiply' needs a numeric 'value'")
        if self.type == "multiply":
            try:
                float(str(self.value).replace(",", "."))
            except ValueError as exc:
                raise ValueError("'multiply' needs a numeric 'value'") from exc
        if self.type == "round" and self.decimals is None:
            raise ValueError("'round' needs 'decimals'")
        if self.type in {"prefix", "suffix"} and self.value in (None, ""):
            raise ValueError(f"'{self.type}' needs a 'value'")
        return self


class Lookup(_Strict):
    """The cell holds a code; the stored value is `value_column` of the row of `sheet`
    whose `key_column` equals that code. A code that isn't found is an error, never blank."""

    sheet: str
    key_column: str
    value_column: str
    header_row: int = Field(default=1, ge=1)


class ColumnRule(_Strict):
    source: str = Field(min_length=1, max_length=255)
    target: str
    type: ColumnType = "string"
    format: str | None = None  # strptime format for string -> date/datetime
    required: bool = False
    default: str | int | float | bool | None = None
    label: str | None = Field(default=None, max_length=255)
    lookup: Lookup | None = None
    operations: list[Operation] = Field(default_factory=list)


class TableRule(_Strict):
    table: str
    label: str | None = Field(default=None, max_length=120)
    sheet: str | None = None  # defaults to match.sheet
    key: list[str] = Field(default_factory=list)  # target columns; empty = de-duplicate by row hash
    columns: list[ColumnRule] = Field(min_length=1)


class Match(_Strict):
    sheet: str | None = None
    required_headers: list[str] = Field(default_factory=list)


class ProfileDefinition(_Strict):
    match: Match = Field(default_factory=Match)
    header_row: int = Field(default=1, ge=1)
    first_data_row: int | None = Field(default=None, ge=1)
    stop_on_blank_rows: int = Field(default=3, ge=1, le=1000)
    strict: bool = False  # any bad row aborts the whole import
    tables: list[TableRule] = Field(min_length=1)

    @property
    def data_start_row(self) -> int:
        return self.first_data_row or self.header_row + 1

    @model_validator(mode="after")
    def _check(self) -> ProfileDefinition:
        if self.first_data_row is not None and self.first_data_row <= self.header_row:
            raise ValueError("'first_data_row' must come after 'header_row'")
        seen_tables: set[str] = set()
        for t_i, t in enumerate(self.tables):
            where = f"tables[{t_i}]"
            if not IDENT_RE.match(t.table) or t.table in RESERVED_TABLES:
                raise ValueError(
                    f"{where}.table '{t.table}' must be lowercase letters, digits, '_' "
                    "(starting with a letter) and not a reserved name"
                )
            if t.table in seen_tables:
                raise ValueError(f"{where}: table '{t.table}' is defined twice")
            seen_tables.add(t.table)
            targets: set[str] = set()
            for c_i, c in enumerate(t.columns):
                cwhere = f"{where}.columns[{c_i}]"
                if not IDENT_RE.match(c.target):
                    raise ValueError(
                        f"{cwhere}.target '{c.target}' must be lowercase letters, digits, '_' "
                        "(starting with a letter)"
                    )
                if c.target in targets:
                    raise ValueError(f"{cwhere}: target '{c.target}' is used twice")
                targets.add(c.target)
                for op in c.operations:
                    if op.type in NUMERIC_OPS and c.type not in NUMERIC_TYPES:
                        raise ValueError(
                            f"{cwhere}: '{op.type}' needs a number/integer column, not {c.type}"
                        )
                    if op.type in STRING_OPS and c.type != "string":
                        raise ValueError(
                            f"{cwhere}: '{op.type}' needs a string column, not {c.type}"
                        )
            if len(set(t.key)) != len(t.key):
                raise ValueError(f"{where}.key lists a column twice")
            for k in t.key:
                if k not in targets:
                    raise ValueError(f"{where}.key: '{k}' is not a target column of this table")
        return self


def parse_definition(raw: Any) -> ProfileDefinition:
    """Validate a profile definition; raise ValidationFailed with one detail per problem."""
    try:
        return ProfileDefinition.model_validate(raw)
    except ValidationError as exc:
        details = []
        for err in exc.errors():
            path = ".".join(str(p) for p in err["loc"])
            msg = err["msg"].removeprefix("Value error, ")
            details.append({"path": path, "message": msg})
        raise ValidationFailed("Invalid import profile", details=details) from exc


def resolve_sheet(defn: ProfileDefinition, table: TableRule) -> str | None:
    return table.sheet or defn.match.sheet


def score_profile(defn: ProfileDefinition, sheets: dict[str, list[str]]) -> dict[str, Any]:
    """How well a profile fits a workbook: share of expected headers that are present."""
    best: dict[str, Any] = {"score": 0.0, "sheet": None, "matched": [], "missing": []}
    wanted_sheet = norm_header(defn.match.sheet) if defn.match.sheet else None
    for name, headers in sheets.items():
        if wanted_sheet and norm_header(name) != wanted_sheet:
            continue
        have = {norm_header(h) for h in headers}
        wanted = defn.match.required_headers or [
            c.source for t in defn.tables for c in t.columns if c.source
        ]
        wanted = list(dict.fromkeys(wanted))
        matched = [w for w in wanted if norm_header(w) in have]
        missing = [w for w in wanted if norm_header(w) not in have]
        score = len(matched) / len(wanted) if wanted else 0.0
        if score > best["score"] or best["sheet"] is None:
            best = {"score": round(score, 3), "sheet": name, "matched": matched, "missing": missing}
    return best


def used_headers(defn: ProfileDefinition) -> set[str]:
    out: set[str] = set()
    for t in defn.tables:
        for c in t.columns:
            out.add(norm_header(c.source))
    return out
