# Adan's lane — unattended run log

One row per task, appended as each merges. Same format as `ayed-lane-log.md`.

| Timestamp | Task | Outcome | Tokens | Notes |
| --------- | ---- | ------- | ------ | ----- |
| 2026-08-27 09:15 | BE-005 | Merged (PR #47) | ~118k | Scope grew well past "crawler jobs": the task assumed text extraction was solved, but nothing in the repo produced the `StructureMap` chunking needs and no PDF library was declared. Delivered robots.txt compliance, per-brand crawlers with domain-boundary host matching, content-hash change detection, a staging pipeline holding no index capability, and `app/ingestion/structure.py`. pdfplumber (MIT) chosen over PyMuPDF (AGPL, disqualifying here) after probing that it surfaces per-character font size and ruling-line table detection rather than flat text. Three review rounds; rounds 1-2 were each a one-dimensional threshold tightening that opened a symmetric failure on the other side, so round 3 switched to signals actually present in the data. **Open limitation:** a table continued across a page break with neither a repeated header nor a "(continued)" banner stays two blocks. Geometry was measured as a candidate signal and rejected — it does not separate the two cases. Recorded in the tracker rather than papered over. |
