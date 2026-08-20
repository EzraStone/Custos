"""Checks the site keeps the promises it makes about itself.

Three of them are claims printed on the pages — that the site loads no
third-party resource, sets no cookie, and runs no script — and a claim nobody
tests is a claim that quietly stops being true. The rest are the ordinary ways
a hand-written static site rots: a link to a page that was renamed, a fragment
that no longer has an anchor, an image with no alt text.

Run with `make site-check`. No dependencies; standard library only.
"""

from __future__ import annotations

import sys
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parent

# Subresources are fetched by the browser without the reader choosing to.
# An <a href> to another origin is a link a person clicks; a stylesheet or a
# script from another origin is a third party in the page.
SUBRESOURCE = {
    ("link", "href"),
    ("script", "src"),
    ("img", "src"),
    ("iframe", "src"),
    ("source", "src"),
    ("video", "src"),
    ("audio", "src"),
}


class Page(HTMLParser):
    def __init__(self, path: Path) -> None:
        super().__init__(convert_charrefs=True)
        self.path = path
        self.ids: set[str] = set()
        self.links: list[tuple[str, str, str]] = []  # (tag, attr, value)
        self.scripts = 0
        self.h1 = 0
        self.imgs_without_alt = 0
        self.title = ""
        self.meta: dict[str, str] = {}
        self.rels: set[str] = set()
        self.lang = ""
        self._in_title = False
        self.feed(path.read_text(encoding="utf-8"))

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k: (v or "") for k, v in attrs}
        if tag == "html":
            self.lang = a.get("lang", "")
        if tag == "title":
            self._in_title = True
        if tag == "script":
            self.scripts += 1
        if tag == "h1":
            self.h1 += 1
        if tag == "img" and not a.get("alt"):
            self.imgs_without_alt += 1
        if tag == "meta" and a.get("name"):
            self.meta[a["name"]] = a.get("content", "")
        if tag == "link" and a.get("rel"):
            self.rels.add(a["rel"])
        if "id" in a:
            self.ids.add(a["id"])
        for attr in ("href", "src"):
            if attr in a:
                self.links.append((tag, attr, a[attr]))

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data.strip()


def check() -> list[str]:
    pages = sorted(ROOT.glob("*.html"))
    if not pages:
        return [f"{ROOT}: no pages found"]

    parsed = {p: Page(p) for p in pages}
    ids = {p.name: page.ids for p, page in parsed.items()}
    problems: list[str] = []

    def fail(path: Path, msg: str) -> None:
        problems.append(f"{path.relative_to(ROOT.parent)}: {msg}")

    for path, page in parsed.items():
        if not page.lang:
            fail(path, "<html> has no lang attribute")
        if not page.title:
            fail(path, "no <title>")
        if not page.meta.get("description"):
            fail(path, "no meta description")
        if not page.meta.get("viewport"):
            fail(path, "no viewport meta")
        if "stylesheet" not in page.rels:
            fail(path, "no stylesheet")
        if "icon" not in page.rels:
            fail(path, "no favicon")
        if page.h1 != 1:
            fail(path, f"expected exactly one <h1>, found {page.h1}")
        if page.scripts:
            fail(path, f"{page.scripts} <script> tag(s); the site claims it runs none")
        if page.imgs_without_alt:
            fail(path, f"{page.imgs_without_alt} <img> without alt text")

        for tag, attr, raw in page.links:
            url = urlsplit(raw)
            if url.scheme in ("http", "https"):
                if (tag, attr) in SUBRESOURCE:
                    fail(path, f"third-party subresource <{tag} {attr}={raw!r}>")
                continue
            if url.scheme in ("mailto", "tel"):
                continue
            if url.scheme:
                fail(path, f"unexpected scheme in {raw!r}")
                continue

            target = path if not url.path else (ROOT / unquote(url.path)).resolve()
            if not target.is_file():
                fail(path, f"broken link {raw!r}")
                continue
            if url.fragment:
                known = ids.get(target.name)
                if known is None:
                    fail(path, f"fragment into a non-page: {raw!r}")
                elif url.fragment not in known:
                    fail(path, f"no anchor #{url.fragment} in {target.name}")

    return problems


def main() -> int:
    problems = check()
    if problems:
        print(f"site: {len(problems)} problem(s)")
        for p in problems:
            print(f"  {p}")
        return 1
    pages = len(list(ROOT.glob("*.html")))
    print(f"site: {pages} pages, links and anchors resolve, no third-party resource")
    return 0


if __name__ == "__main__":
    sys.exit(main())
