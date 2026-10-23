"""Panel design: the tool-neutral project model's logic and its outputs."""

#: The version of the calculation rules and tables a design is made under.
#: Bump it whenever a sizing rule, a table, a note's meaning or the findings
#: policy changes: a saved revision designed under another version is stale,
#: and is refused for export rather than silently re-designed under this one.
RULES_VERSION = "2026.10.23.1"

#: The parts catalogue a design selects articles from. None is wired into the
#: design yet (devices carry ratings, not articles), so it is ``"none"``.
CATALOGUE_VERSION = "none"
