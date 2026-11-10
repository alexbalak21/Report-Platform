"""HTML -> PDF with headless Chromium (Playwright).

Why a dedicated thread: Playwright's sync API objects belong to the thread that created them, and
gunicorn serves requests from a thread pool. One worker thread owns the browser; callers hand it
work through a queue and wait for the result. PDFs are therefore produced one at a time per
process, which is plenty for tens of users and avoids running several Chromiums at once.

Print layout
    The shared renderer emits header / body / footer as <thead>/<tbody>/<tfoot> so they repeat on
    every page. Page numbers can't be baked into a repeated element, so when the header or
    footer uses {{page}} / {{pages}} that region is moved into Chromium's own header/footer
    template (which does know the page number) and the margin grows to make room for it.

Safety
    JavaScript is disabled and every request except data: URIs and `allowed_hosts` is aborted.
"""

from __future__ import annotations

import logging
import queue
import re
import threading
from concurrent.futures import Future
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlparse

from lxml import html as lxml_html
from rp_shared.template_render import PAGE_TOKEN, PAGES_TOKEN

log = logging.getLogger(__name__)

_MARGIN_RE = re.compile(
    r"@page\s*\{[^}]*?margin:\s*([\d.]+)mm\s+([\d.]+)mm\s+([\d.]+)mm\s+([\d.]+)mm"
)
_HEIGHT_RE = re.compile(r"height:\s*([\d.]+)mm")
_PAGE_SPANS = (
    (PAGE_TOKEN, '<span class="pageNumber"></span>'),
    (PAGES_TOKEN, '<span class="totalPages"></span>'),
)


class PdfError(Exception):
    pass


class PdfRenderer(Protocol):
    def render(self, html: str) -> bytes: ...


@dataclass(frozen=True)
class PrintJob:
    """What Chromium needs: the body HTML and, when page numbers are used, the templates."""

    html: str
    header_template: str | None = None
    footer_template: str | None = None
    margin_mm: tuple[float, float, float, float] | None = None  # top, right, bottom, left


def _strip_tokens(text: str) -> str:
    return text.replace(PAGE_TOKEN, "").replace(PAGES_TOKEN, "")


def _with_spans(text: str) -> str:
    for token, span in _PAGE_SPANS:
        text = text.replace(token, span)
    return text


def prepare_print(html: str) -> PrintJob:
    """Split page-numbered header/footer out of the document (see module docstring)."""
    if PAGE_TOKEN not in html and PAGES_TOKEN not in html:
        return PrintJob(html=html)
    doc = lxml_html.document_fromstring(html)
    css = "".join(s.text_content() for s in doc.xpath("//style"))
    m = _MARGIN_RE.search(css)
    if not m:
        return PrintJob(html=_strip_tokens(html))
    top, right, bottom, left = (float(v) for v in m.groups())

    templates: dict[str, str | None] = {"rp-head": None, "rp-foot": None}
    for cls in templates:
        cells = doc.xpath(f'//td[contains(concat(" ", normalize-space(@class), " "), " {cls} ")]')
        if not cells:
            continue
        cell = cells[0]
        inner = "".join(
            lxml_html.tostring(child, encoding="unicode") for child in cell.iterchildren()
        )
        if PAGE_TOKEN not in inner and PAGES_TOKEN not in inner:
            continue
        height = float(h.group(1)) if (h := _HEIGHT_RE.search(cell.get("style", ""))) else 12.0
        block_css = "".join(s.text_content() for s in doc.xpath("//style")[1:])
        templates[cls] = (
            f"<style>{block_css}</style>"
            f'<div style="box-sizing:border-box;width:100%;padding:0 {right}mm 0 {left}mm;'
            f'font:10pt/1.4 Arial,sans-serif;color:#111">{_with_spans(inner)}</div>'
        )
        # The region now lives in Chromium's margin: drop it from the page and grow the margin.
        row = cell.getparent()
        section = row.getparent()
        section.getparent().remove(section)
        if cls == "rp-head":
            top += height
        else:
            bottom += height
    if templates["rp-head"] is None and templates["rp-foot"] is None:
        return PrintJob(html=_strip_tokens(html))
    body = lxml_html.tostring(doc, encoding="unicode", doctype="<!doctype html>")
    return PrintJob(
        html=_strip_tokens(body),
        header_template=templates["rp-head"],
        footer_template=templates["rp-foot"],
        margin_mm=(top, right, bottom, left),
    )


class PlaywrightPdfRenderer:
    def __init__(self, timeout_ms: int = 30_000, allowed_hosts: tuple[str, ...] = ()) -> None:
        self.timeout_ms = timeout_ms
        self.allowed_hosts = frozenset(h.lower() for h in allowed_hosts)
        self._queue: queue.Queue = queue.Queue()
        self._thread: threading.Thread | None = None
        self._start_lock = threading.Lock()

    # -- public ---------------------------------------------------------------------------
    def render(self, html: str) -> bytes:
        self._ensure_thread()
        future: Future = Future()
        self._queue.put((prepare_print(html), future))
        try:
            return future.result(timeout=self.timeout_ms / 1000 + 10)
        except PdfError:
            raise
        except Exception as exc:
            raise PdfError(f"PDF rendering failed: {exc}") from exc

    def close(self) -> None:
        if self._thread is not None:
            self._queue.put(None)
            self._thread.join(timeout=10)
            self._thread = None

    # -- worker thread --------------------------------------------------------------------
    def _ensure_thread(self) -> None:
        with self._start_lock:
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._loop, daemon=True, name="pdf")
                self._thread.start()

    def _allow(self, url: str) -> bool:
        if url.startswith(("data:", "about:")):
            return True
        host = (urlparse(url).hostname or "").lower()
        return host in self.allowed_hosts

    def _loop(self) -> None:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            try:
                while True:
                    item = self._queue.get()
                    if item is None:
                        return
                    job, future = item
                    try:
                        if not browser.is_connected():
                            browser = pw.chromium.launch()
                        future.set_result(self._print(browser, job))
                    except Exception as exc:
                        log.warning("pdf job failed", exc_info=True)
                        future.set_exception(PdfError(str(exc)))
            finally:
                browser.close()

    def _print(self, browser, job: PrintJob) -> bytes:
        context = browser.new_context(java_script_enabled=False)
        try:
            context.route(
                "**/*",
                lambda route: (
                    route.continue_() if self._allow(route.request.url) else route.abort()
                ),
            )
            page = context.new_page()
            page.set_default_timeout(self.timeout_ms)
            page.set_content(job.html, wait_until="load")
            options: dict = {"format": "A4", "print_background": True}
            if job.header_template or job.footer_template:
                t, r, b, left = job.margin_mm
                options.update(
                    display_header_footer=True,
                    header_template=job.header_template or "<span></span>",
                    footer_template=job.footer_template or "<span></span>",
                    margin={
                        "top": f"{t}mm",
                        "right": f"{r}mm",
                        "bottom": f"{b}mm",
                        "left": f"{left}mm",
                    },
                    prefer_css_page_size=False,
                )
            else:
                options["prefer_css_page_size"] = True
            return page.pdf(**options)
        finally:
            context.close()
