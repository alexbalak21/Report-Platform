import pytest
from app.policy import allowed, min_role


@pytest.mark.parametrize(
    "path,method,expected",
    [
        ("reports", "GET", "viewer"),
        ("blocks/abc", "GET", "viewer"),
        ("blocks", "POST", "designer"),
        ("blocks/abc", "PUT", "designer"),
        ("blocks/preview", "POST", "operator"),
        ("templates/abc/render", "POST", "operator"),
        ("templates/abc/publish", "POST", "designer"),
        ("ingestion/uploads", "POST", "operator"),
        ("ingestion/profiles", "POST", "designer"),
        ("ingestion/profiles/1", "DELETE", "designer"),
        ("reports", "POST", "operator"),
        ("reports/1/finalize", "POST", "operator"),
        ("editor/documents/1", "PUT", "operator"),
        ("ingestion/tables/samples/rows/1", "PATCH", None),  # internal write-back
        ("ingestion/tables/samples/rows/1", "GET", "viewer"),
        ("auth/login", "POST", None),
        ("nonsense", "GET", None),
    ],
)
def test_min_role(path, method, expected):
    assert min_role(path, method) == expected


def test_role_hierarchy():
    assert allowed("admin", "designer") and allowed("designer", "operator")
    assert allowed("operator", "viewer") and not allowed("viewer", "operator")
    assert not allowed("operator", "designer") and not allowed("unknown", "viewer")
