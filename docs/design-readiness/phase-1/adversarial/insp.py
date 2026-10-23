import json, sys, os, re
import pdfplumber
d = sys.argv[1]
NEED = {"draft": "NOT FOR EXECUTION", "approved_label": "REVIEW APPROVED"}
FORBID = re.compile(r"ready for execution|for construction|issued for|\bissued\b", re.I)
rows = []
for f in sorted(os.listdir(d)):
    state, fmt = f.split(".", 1)
    p = os.path.join(d, f)
    if fmt.endswith("pdf"):
        with pdfplumber.open(p) as pdf:
            pages = [pg.extract_text() or "" for pg in pdf.pages]
        units = pages
    else:
        body = open(p, encoding="utf-8", errors="replace").read()
        units = [body]
    text = "\n".join(units)
    rows.append({
        "state": state, "format": fmt, "units": len(units),
        "draft_mark_units": sum("NOT FOR EXECUTION" in u for u in units),
        "review_mark_units": sum("REVIEW APPROVED" in u for u in units),
        "forged": "FORGED" in text,
        "approver_X": bool(re.search(r"approved by X\b", text)),
        "hash": "40beff92" in text,
        "rules": "2026.10.23.1" in text,
        "forbidden": sorted(set(m.group(0).lower() for m in FORBID.finditer(text))),
    })
print(json.dumps(rows))
