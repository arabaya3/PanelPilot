"""Panel design: the tool-neutral project model's logic and its outputs."""

#: The version of the calculation rules and tables a design is made under.
#: Bump it whenever a sizing rule, a table, a note's meaning or the findings
#: policy changes: a saved revision designed under another version is stale,
#: and is refused for export rather than silently re-designed under this one.
RULES_VERSION = "2026.11.03.1"

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
RENDERER_VERSION = "2026.11.11.2"

#: The renderer version of each snapshot schema, by schema marker: what an
#: approval of a revision stored in that schema records and is compared with.
#: ``pp-design/1`` keeps :data:`RENDERER_VERSION`, so no v1 approval is
#: touched by a v2 renderer change. ``pp-design/2`` exports print the v2
#: structural findings and add the review schedules and (M4) the connection
#: drawing, so their content differs
#: from the v1 export of the same design and has its own version. Bump the v2
#: entry for any change of what a v2 export says
#: (``tests/design/test_renderer_version.py`` holds both to their fingerprints).
RENDERER_VERSIONS: dict[str, str] = {
    "pp-design/1": RENDERER_VERSION,
    "pp-design/2": "2026.11.11.v2-26",
    # M7a: a v3 export is a v2 export plus the board-to-board links (the wire
    # schedule's section, the drawing's link sheets, the JSON's ``links``)
    # and their findings. Bump it for any change of what a v3 export says.
    "pp-design/3": "2026.11.11.v3-4",
}


def renderer_version(schema: str) -> str:
    """The renderer version for a snapshot schema; :data:`RENDERER_VERSION` for v1.

    Args:
        schema: A schema marker (``pp-design/1``, ``pp-design/2`` or ``pp-design/3``).

    Returns:
        Its renderer version.

    Raises:
        KeyError: For a schema this software does not render.
    """
    return RENDERER_VERSIONS[schema]


#: What an approval recorded before renderer versions were (a NULL) is read
#: as. Which renderer exported it was never recorded, and export content did
#: change while such approvals existed, so it matches no real version: those
#: approvals stop counting and must be granted again. Never change it.
RENDERER_BASELINE = "unversioned"
