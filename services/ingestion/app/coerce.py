"""Cell value -> typed Python value, and the per-column operation chain.

Rules (kept deliberately strict, so bad data is reported instead of guessed):
  * empty string / whitespace -> None
  * numbers accept decimal commas ("12,5"), spaces and NBSP as thousands separators;
    when both ',' and '.' appear the last one is the decimal separator
  * dates accept real dates, Excel serial numbers, and strings in the profile `format` or ISO;
    without a format dd/mm/yyyy is also accepted (the desktop app's default)
  * booleans accept yes/no, oui/non, vrai/faux, true/false, 1/0
"""

from __future__ import annotations

import math
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from openpyxl.utils.datetime import from_excel
from rp_shared.placeholders import Op, apply_op

TRUE_WORDS = {"true", "yes", "y", "oui", "o", "vrai", "1", "x"}
FALSE_WORDS = {"false", "no", "n", "non", "faux", "0"}
ISO_FORMATS = ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S")
FR_FORMATS = ("%d/%m/%Y", "%d/%m/%Y %H:%M:%S", "%d-%m-%Y", "%d.%m.%Y")
MAX_EXCEL_SERIAL = 2_958_465  # 31 Dec 9999


class CoerceError(ValueError):
    pass


def _number(v: Any) -> Decimal:
    if isinstance(v, bool):
        raise CoerceError("expected a number, got a yes/no value")
    if isinstance(v, Decimal):
        d = v
    elif isinstance(v, int):
        d = Decimal(v)
    elif isinstance(v, float):
        if not math.isfinite(v):
            raise CoerceError("not a finite number")
        d = Decimal(repr(v))
    elif isinstance(v, str):
        s = v
        for ch in (" ", " ", " "):
            s = s.replace(ch, "")
        if "," in s and "." in s:
            if s.rfind(",") > s.rfind("."):
                s = s.replace(".", "").replace(",", ".")
            else:
                s = s.replace(",", "")
        else:
            s = s.replace(",", ".")
        try:
            d = Decimal(s)
        except InvalidOperation as exc:
            raise CoerceError(f"'{v}' is not a number") from exc
    else:
        raise CoerceError(f"expected a number, got {type(v).__name__}")
    if not d.is_finite():
        raise CoerceError("not a finite number")
    return d


def _integer(v: Any) -> int:
    d = _number(v)
    if d != d.to_integral_value():
        raise CoerceError(f"'{v}' is not a whole number")
    return int(d)


def _parse_datetime(s: str, fmt: str | None) -> datetime:
    formats = (fmt, *ISO_FORMATS) if fmt else (*ISO_FORMATS, *FR_FORMATS)
    for f in formats:
        try:
            return datetime.strptime(s, f)  # noqa: DTZ007 - Excel dates are naive
        except ValueError:
            continue
    hint = f" (expected {fmt})" if fmt else ""
    raise CoerceError(f"'{s}' is not a valid date{hint}")


def _datetime(v: Any, fmt: str | None) -> datetime:
    if isinstance(v, datetime):
        return v
    if isinstance(v, date):
        return datetime(v.year, v.month, v.day)  # noqa: DTZ001 - Excel dates are naive
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        if not (1 <= v < MAX_EXCEL_SERIAL):
            raise CoerceError(f"{v} is not a valid Excel date")
        return from_excel(v)
    if isinstance(v, str):
        return _parse_datetime(v, fmt)
    raise CoerceError(f"expected a date, got {type(v).__name__}")


def _date(v: Any, fmt: str | None) -> date:
    return _datetime(v, fmt).date()


def _boolean(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    word = str(v).strip().casefold()
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        word = str(int(v)) if float(v).is_integer() else word
    if word in TRUE_WORDS:
        return True
    if word in FALSE_WORDS:
        return False
    raise CoerceError(f"'{v}' is not a yes/no value")


def _string(v: Any) -> str:
    if isinstance(v, float):
        return str(int(v)) if v.is_integer() else format(Decimal(repr(v)), "f")
    if isinstance(v, Decimal):
        return format(v.normalize(), "f")
    if isinstance(v, datetime):
        return v.isoformat(sep=" ") if v.time() != datetime.min.time() else v.date().isoformat()
    return str(v)


def coerce(value: Any, typ: str, fmt: str | None = None) -> Any:
    if isinstance(value, str):
        value = value.replace(" ", " ").strip()
        if value == "":
            return None
    if value is None:
        return None
    if typ == "string":
        s = _string(value).strip()
        return s or None
    if typ == "number":
        return _number(value)
    if typ == "integer":
        return _integer(value)
    if typ == "date":
        return _date(value, fmt)
    if typ == "datetime":
        return _datetime(value, fmt)
    if typ == "boolean":
        return _boolean(value)
    raise CoerceError(f"unknown type '{typ}'")


def apply_operations(value: Any, operations: list, typ: str) -> Any:
    """Run the profile's operation chain left to right (multiply -> round -> ...)."""
    if value is None:
        return None
    try:
        for op in operations:
            t = op.type
            if t == "multiply":
                value = apply_op(value, Op("multiply", str(op.value)))
            elif t == "round":
                value = apply_op(value, Op("round", str(op.decimals or 0)))
            elif t == "uppercase":
                value = str(value).upper()
            elif t == "lowercase":
                value = str(value).lower()
            elif t == "strip":
                value = str(value).strip()
            elif t == "prefix":
                value = f"{op.value}{value}"
            elif t == "suffix":
                value = f"{value}{op.value}"
    except (ValueError, InvalidOperation) as exc:
        raise CoerceError(str(exc)) from exc
    if typ == "integer" and isinstance(value, Decimal):
        return _integer(value)
    return value
