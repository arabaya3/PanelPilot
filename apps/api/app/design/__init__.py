"""Panel design: the tool-neutral project model's logic and its outputs."""

#: The version of the calculation rules and tables a design is made under.
#: Bump it whenever a sizing rule, a table, a note's meaning or the findings
#: policy changes: a saved revision designed under another version is stale,
#: and is refused for export rather than silently re-designed under this one.
RULES_VERSION = "2026.10.23.1"

#: The parts catalogue a design selects articles from. None is wired into the
#: design yet (devices carry ratings, not articles), so it is ``"none"``.
CATALOGUE_VERSION = "none"

#: The version of what the available exports print from a stored design: the
#: words, figures, designations, marks and drawing content of the drawing,
#: calculations and quotation PDFs, the DXF and the project JSON. An approval
#: records it, and counts only while it is unchanged, so a material renderer
#: change refuses an approved revision's export (``revision_stale``) instead of
#: printing a different file as the same approval. Bump it for any change that
#: alters that content; ``tests/design/test_renderer_version.py`` fails when
#: the content of its reference exports changes and this does not. What is
#: material is set out in ``docs/design-readiness/phase-1-export-integrity.md``
#: ("Renderer version").
RENDERER_VERSION = "2026.10.31.3"

#: What an approval recorded before renderer versions were (a NULL) is read
#: as. Which renderer exported it was never recorded, and export content did
#: change while such approvals existed, so it matches no real version: those
#: approvals stop counting and must be granted again. Never change it.
RENDERER_BASELINE = "unversioned"
