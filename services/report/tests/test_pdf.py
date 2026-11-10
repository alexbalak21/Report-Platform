"""prepare_print is unit-tested; the Chromium tests print real PDFs and read them back."""

import io
import re

import pytest
from app.pdf import PdfError, PlaywrightPdfRenderer, prepare_print
from conftest import doc, inst
from rp_shared.schemas.template_document import parse_template_document
from rp_shared.template_render import PAGE_TOKEN, PAGES_TOKEN, collect_pins, render_document


def build(blocks, tables=None, **settings):
    d = parse_template_document(
        doc(
            body=[inst(blocks.table, "t", source="samples")],
            header=[inst(blocks.header, "hd")],
            footer=[inst(blocks.footer, "ft")],
            **settings,
        )
    )
    out = render_document(
        d,
        collect_pins(d, blocks),
        context={"lot": "L1"},
        tables=tables or {"samples": [{"lot": f"L{i:03d}"} for i in range(150)]},
        mode="print",
        report_number="260101-1",
    )
    return out.html


# ---------------------------------------------------------------------------
# prepare_print (no browser needed)
# ---------------------------------------------------------------------------
def test_html_without_page_numbers_is_printed_as_is(blocks):
    html = build(blocks, page_numbering=False)
    job = prepare_print(html)
    assert job.html == html and job.footer_template is None and job.margin_mm is None


def test_footer_with_page_numbers_moves_into_the_chromium_footer(blocks):
    html = build(blocks)
    assert PAGE_TOKEN in html and PAGES_TOKEN in html
    job = prepare_print(html)
    assert job.footer_template and job.header_template is None
    assert '<span class="pageNumber"></span>' in job.footer_template
    assert '<span class="totalPages"></span>' in job.footer_template
    assert PAGE_TOKEN not in job.html and PAGES_TOKEN not in job.html
    assert "Page " not in job.html and "<tfoot" not in job.html  # moved, not duplicated
    assert "Rapport N° 260101-1" in job.html  # the header (no page number) stays in the page
    top, right, bottom, left = job.margin_mm
    assert (top, right, left) == (20, 15, 15) and bottom == 20 + 15  # + the footer's 15 mm


def test_page_tokens_in_the_body_are_just_removed(blocks, templates):
    d = parse_template_document(doc(body=[inst(blocks.footer, "f")]))  # footer block in the body
    html = render_document(d, collect_pins(d, blocks), context={}, mode="print").html
    job = prepare_print(html)
    assert job.footer_template is None and PAGE_TOKEN not in job.html
    assert "Page  /" in job.html


# ---------------------------------------------------------------------------
# Real Chromium
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def chromium():
    pytest.importorskip("playwright")
    pypdf = pytest.importorskip("pypdf")
    renderer = PlaywrightPdfRenderer(timeout_ms=30_000)
    try:
        renderer.render("<p>probe</p>")
    except (PdfError, Exception) as exc:  # browser not installed in this environment
        renderer.close()
        pytest.skip(f"Chromium is not available: {exc}")
    yield renderer, pypdf
    renderer.close()


def pages_text(pypdf, pdf: bytes) -> list[str]:
    reader = pypdf.PdfReader(io.BytesIO(pdf))
    # pypdf reads the font's hyphen glyph back as a private-use character; map it to "-".
    return [re.sub(r"[\ue000-\uf8ff]", "-", p.extract_text()) for p in reader.pages]


def test_every_page_has_its_number_and_the_repeating_header(chromium, blocks):
    renderer, pypdf = chromium
    pdf = renderer.render(build(blocks))
    assert pdf.startswith(b"%PDF-")
    texts = pages_text(pypdf, pdf)
    total = len(texts)
    assert total >= 3
    for number, text in enumerate(texts, start=1):
        assert f"Page {number} / {total}" in text, f"page {number}"
        assert "Rapport N° 260101-1" in text, f"header missing on page {number}"
    joined = "\n".join(texts)
    assert "L000" in joined and "L149" in joined  # all rows made it, none lost at a page break


def test_without_page_numbers_it_still_paginates(chromium, blocks):
    renderer, pypdf = chromium
    texts = pages_text(pypdf, renderer.render(build(blocks, page_numbering=False)))
    assert len(texts) >= 3 and all("Rapport N° 260101-1" in t for t in texts)
    assert not any(re.search(r"Page \d", t) for t in texts)  # the numbers are blank


def test_scripts_and_remote_requests_are_blocked(chromium):
    renderer, pypdf = chromium
    html = (
        "<!doctype html><body><p id=x>SAFE</p>"
        "<script>document.getElementById('x').textContent='HACKED'</script>"
        '<img src="http://203.0.113.9/tracker.png"><p>after</p></body>'
    )
    text = pages_text(pypdf, renderer.render(html))[0]
    assert "SAFE" in text and "HACKED" not in text and "after" in text


def test_allowed_hosts_are_the_only_remote_hosts(chromium):
    renderer, _ = chromium
    assert renderer._allow("data:image/png;base64,AAAA")
    assert not renderer._allow("http://evil.example/x.png")
    custom = PlaywrightPdfRenderer(allowed_hosts=("Assets.Internal",))
    assert custom._allow("http://assets.internal/logo.png")
    assert not custom._allow("http://other.internal/logo.png")


def test_concurrent_callers_share_the_one_browser_thread(chromium):
    import threading

    renderer, _ = chromium
    results, errors = [], []

    def work(i):
        try:
            results.append(renderer.render(f"<p>doc {i}</p>"))
        except Exception as exc:  # pragma: no cover - failure path
            errors.append(exc)

    threads = [threading.Thread(target=work, args=(i,)) for i in range(5)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert not errors and len(results) == 5 and all(r.startswith(b"%PDF-") for r in results)
