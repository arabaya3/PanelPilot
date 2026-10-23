"""Inspect exported files for DRAFT marks, approval text and findings.

Run inside the api container (pdfplumber is installed there):
    python inspect.py /tmp/a.pdf /tmp/b.dxf /tmp/c.csv
Prints one JSON line per file.
"""

from __future__ import annotations

import json
import sys

NEEDLES = ("NOT FOR EXECUTION", "DRAFT", "REVIEW APPROVED", "FORGED", "finding", "blocking")


def pdf_pages(path: str) -> list[str]:
    import pdfplumber

    with pdfplumber.open(path) as pdf:
        return [page.extract_text() or "" for page in pdf.pages]


def main(paths: list[str]) -> None:
    for path in paths:
        if path.endswith(".pdf"):
            pages = pdf_pages(path)
            report = {
                "file": path,
                "pages": len(pages),
                "pages_marked_draft": sum("NOT FOR EXECUTION" in p for p in pages),
                "counts": {n: sum(p.lower().count(n.lower()) for p in pages) for n in NEEDLES},
                "first_page_head": pages[0][:600] if pages else "",
                "findings_excerpt": next(
                    (p[p.lower().find("finding"):][:700] for p in pages if "finding" in p.lower()), ""
                ),
            }
        else:
            with open(path, encoding="utf-8", errors="replace") as handle:
                body = handle.read()
            report = {
                "file": path,
                "first_line": body.splitlines()[0] if body else "",
                "counts": {n: body.lower().count(n.lower()) for n in NEEDLES},
            }
        print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main(sys.argv[1:])
