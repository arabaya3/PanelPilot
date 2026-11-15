"""The versioned design model: plain domain values, conversions, schema versions.

``read.read_snapshot`` is the one way a stored snapshot is read; it reads
``pp-design/1`` (bare) and ``pp-design/2`` (enveloped: the v1 design plus
templates, components, strips, nets, connections and wires). ``write`` builds
v2 snapshots; ``versions.WRITTEN_BY_DEFAULT`` says what a save writes.
"""
