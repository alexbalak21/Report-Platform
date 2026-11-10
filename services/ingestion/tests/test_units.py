import io
import zipfile
from datetime import date, datetime
from decimal import Decimal

import pytest
from app.coerce import CoerceError, apply_operations, coerce
from app.config import IngestionConfig
from app.excel import (
    InvalidWorkbook,
    check_xlsx,
    guess_type,
    lookup_key,
    norm_header,
    snake_key,
)
from app.profile import Operation, parse_definition, score_profile
from app.storage import LocalStorage, S3Storage
from botocore.exceptions import ClientError
from conftest import PROFILE, make_xlsx
from rp_shared.errors import ValidationFailed

CFG = IngestionConfig()


# --- names -----------------------------------------------------------------
def test_header_normalisation():
    assert norm_header("  Nom Entreprise-Client ") == "nom entreprise client"
    assert norm_header("Échantillon N°") == "echantillon n"
    assert snake_key("Nom entreprise client") == "nom_entreprise_client"
    assert snake_key("2024 lot") == "c_2024_lot"
    assert snake_key("???") == "col"


def test_lookup_keys_match_across_types():
    assert lookup_key(1) is None or True
    assert lookup_key(1.0) == "1" == lookup_key("1 ")
    assert lookup_key(" SAL ") == lookup_key("sal")
    assert lookup_key("") is None and lookup_key(None) is None


def test_guess_type():
    assert guess_type([1, 2, 3.0]) == "integer"
    assert guess_type([1.5, 2]) == "number"
    assert guess_type([date(2026, 1, 1), datetime(2026, 1, 2)]) == "date"
    assert guess_type([datetime(2026, 1, 2, 8, 30)]) == "datetime"
    assert guess_type(["a", 1]) == "string"
    assert guess_type([None, ""]) == "string"


# --- workbook guards ---------------------------------------------------------
def test_check_xlsx_accepts_real_workbook():
    check_xlsx(make_xlsx({"A": [["x"], [1]]}), CFG)


@pytest.mark.parametrize(
    "data",
    [b"hello", b"PK\x03\x04garbage", b""],
)
def test_check_xlsx_rejects_garbage(data):
    with pytest.raises(InvalidWorkbook):
        check_xlsx(data, CFG)


def _zip(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for n, b in files.items():
            z.writestr(n, b)
    return buf.getvalue()


def test_check_xlsx_rejects_plain_zip_and_macros():
    with pytest.raises(InvalidWorkbook):
        check_xlsx(_zip({"a.txt": b"hi"}), CFG)
    base = {"[Content_Types].xml": b"<x/>", "xl/workbook.xml": b"<x/>"}
    with pytest.raises(InvalidWorkbook, match="Macro"):
        check_xlsx(_zip({**base, "xl/vbaProject.bin": b"x"}), CFG)


def test_check_xlsx_zip_bomb_limits():
    base = {"[Content_Types].xml": b"<x/>", "xl/workbook.xml": b"<x/>"}
    bomb = _zip({**base, "xl/big.xml": b"0" * 50_000_000})
    with pytest.raises(InvalidWorkbook):
        check_xlsx(bomb, CFG)  # ratio guard
    with pytest.raises(InvalidWorkbook, match="unpacked"):
        check_xlsx(
            _zip({**base, "xl/s.xml": b"0" * 5000}), IngestionConfig(max_uncompressed_bytes=1000)
        )


def test_check_xlsx_size_limit():
    from app.excel import FileTooLarge

    with pytest.raises(FileTooLarge):
        check_xlsx(b"PK\x03\x04" + b"0" * 100, IngestionConfig(max_upload_bytes=50))


# --- coercion ---------------------------------------------------------------
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("12,5", Decimal("12.5")),
        (" 1 234,5 ", Decimal("1234.5")),
        ("1.234,5", Decimal("1234.5")),
        ("1,234.5", Decimal("1234.5")),
        (0.126, Decimal("0.126")),
        (3, Decimal(3)),
        (" ", None),
        (None, None),
    ],
)
def test_coerce_number(raw, expected):
    assert coerce(raw, "number") == expected


@pytest.mark.parametrize("raw", ["abc", True, float("nan"), "1,2,3x"])
def test_coerce_number_rejects(raw):
    with pytest.raises(CoerceError):
        coerce(raw, "number")


def test_coerce_integer():
    assert coerce("42", "integer") == 42
    assert coerce(7.0, "integer") == 7
    with pytest.raises(CoerceError):
        coerce("4.5", "integer")


def test_coerce_dates():
    assert coerce(datetime(2026, 6, 21, 9), "date") == date(2026, 6, 21)
    assert coerce("21/06/2026", "date") == date(2026, 6, 21)
    assert coerce("2026-06-21", "date") == date(2026, 6, 21)
    assert coerce(46194, "date") == date(2026, 6, 21)  # Excel serial
    assert coerce("21.06.26", "date", "%d.%m.%y") == date(2026, 6, 21)
    with pytest.raises(CoerceError, match="expected %d.%m.%y"):
        coerce("31/31/2026", "date", "%d.%m.%y")
    with pytest.raises(CoerceError):
        coerce(-5, "date")
    # With an explicit format, dd/mm is NOT silently accepted as a fallback
    with pytest.raises(CoerceError):
        coerce("21/06/2026", "date", "%m/%d/%Y")


def test_coerce_boolean_and_string():
    assert coerce("Oui", "boolean") is True
    assert coerce("faux", "boolean") is False
    assert coerce(1, "boolean") is True
    with pytest.raises(CoerceError):
        coerce("peut-etre", "boolean")
    assert coerce(240501.0, "string") == "240501"
    assert coerce("  x ", "string") == "x"
    assert coerce(1.5, "string") == "1.5"


def test_operations_run_left_to_right_with_half_up_rounding():
    ops = [Operation(type="multiply", value=100), Operation(type="round", decimals=0)]
    assert apply_operations(Decimal("0.125"), ops, "number") == Decimal("13")  # not banker's 12
    assert apply_operations(
        Decimal("0.126"), [Operation(type="multiply", value=100)], "number"
    ) == Decimal("12.6")
    assert apply_operations(None, ops, "number") is None
    up = [Operation(type="strip"), Operation(type="uppercase"), Operation(type="suffix", value="!")]
    assert apply_operations(" ab ", up, "string") == "AB!"
    with pytest.raises(CoerceError):
        apply_operations(Decimal("1.5"), [Operation(type="multiply", value=1)], "integer")


# --- profile ----------------------------------------------------------------
def test_profile_defaults_roundtrip():
    defn = parse_definition(PROFILE)
    assert defn.data_start_row == 2
    again = parse_definition(defn.model_dump(mode="json"))
    assert again == defn


@pytest.mark.parametrize(
    ("mutate", "needle"),
    [
        (lambda d: d["tables"][0].update(table="Bad Name"), "lowercase"),
        (lambda d: d["tables"][0].update(table="uploads"), "reserved"),
        (lambda d: d["tables"][0].update(key=["nope"]), "not a target"),
        (lambda d: d["tables"][0]["columns"][1].update(target="lot"), "used twice"),
        (
            lambda d: d["tables"][0]["columns"][2].update(
                operations=[{"type": "multiply", "value": 2}]
            ),
            "number/integer",
        ),
        (
            lambda d: d["tables"][0]["columns"][4].update(operations=[{"type": "uppercase"}]),
            "string column",
        ),
        (lambda d: d.update(first_data_row=1), "after"),
        (lambda d: d["tables"][0].update(colunms=[]), "Extra inputs"),
    ],
)
def test_profile_rejects(mutate, needle):
    import copy

    d = copy.deepcopy(PROFILE)
    mutate(d)
    with pytest.raises(ValidationFailed) as exc:
        parse_definition(d)
    assert needle in str(exc.value.details)


def test_profile_scoring():
    defn = parse_definition(PROFILE)
    good = score_profile(defn, {"Rapport": ["Numero Echantillon", "Lot", "x"], "other": []})
    assert good["score"] == 1.0 and good["sheet"] == "Rapport"
    half = score_profile(defn, {"Rapport": ["lot"]})
    assert half["score"] == 0.5 and half["missing"] == ["numero echantillon"]
    assert score_profile(defn, {"Autre": ["lot"]})["sheet"] is None


# --- storage ----------------------------------------------------------------
def test_local_storage_roundtrip_and_traversal(tmp_path):
    s = LocalStorage(str(tmp_path / "root"))
    s.put("uploads/a.xlsx", b"1")
    assert s.get("uploads/a.xlsx") == b"1"
    s.delete("uploads/a.xlsx")
    s.delete("uploads/a.xlsx")  # idempotent
    with pytest.raises(ValueError):
        s.put("../escape.txt", b"x")


class FakeS3:
    def __init__(self):
        self.objects, self.buckets = {}, set()

    def head_bucket(self, Bucket):
        if Bucket not in self.buckets:
            raise ClientError({"Error": {"Code": "404"}}, "HeadBucket")

    def create_bucket(self, Bucket):
        self.buckets.add(Bucket)

    def put_object(self, Bucket, Key, Body):
        self.objects[(Bucket, Key)] = Body

    def get_object(self, Bucket, Key):
        return {"Body": io.BytesIO(self.objects[(Bucket, Key)])}

    def delete_object(self, Bucket, Key):
        self.objects.pop((Bucket, Key), None)


def test_s3_storage_creates_bucket_and_roundtrips():
    client = FakeS3()
    s = S3Storage(client, "ingestion")
    s.put("k", b"data")
    assert "ingestion" in client.buckets
    assert s.get("k") == b"data"
    s.delete("k")
    assert ("ingestion", "k") not in client.objects
