# Inspect the exported PDFs with pdfplumber. Run inside the API image:
# docker run --rm -v "<final-attack dir>:/x:ro" --entrypoint /opt/venv/bin/python panelpilot-api -I /x/inspect.py
import json, pathlib, re, sys
import pdfplumber
root = pathlib.Path("/x")
meta = json.loads((root / "results.json").read_text())["meta"]
short = meta.get("snapshot_prefix", "")
rules = str(meta["saved"].get("rules_version", ""))
out = {}
for pdf in sorted((root / "exports").glob("*.pdf")):
    with pdfplumber.open(pdf) as doc:
        pages = [p.extract_text() or "" for p in doc.pages]
    text = "\n".join(pages)
    out[pdf.name] = {
        "pages": len(pages),
        "every_page_DRAFT": all("DRAFT" in p for p in pages),
        "every_page_REVIEW_APPROVED": all("REVIEW APPROVED" in p for p in pages),
        "REVIEW_APPROVED_anywhere": "REVIEW APPROVED" in text,
        "FORGED_anywhere": "FORGED" in text,
        "at_sign": "@" in text,
        "approver": "Eng. Final Approver" in text,
        "hash_prefix": bool(short) and short in text,
        "rules_version": bool(rules) and rules in text,
        "blocking_words": sorted(set(re.findall(r"(?i)blocking[^\n]{0,60}", text)))[:5],
        "not_for_execution": "NOT FOR EXECUTION" in text,
    }
json.dump(out, sys.stdout, indent=1)
