# M9b2 — Guided editing of pattern connections

Status: draft, 2026-11-16. Builds on `main` after #220 (`1ce6f0d`). Not for
deployment. Completes the guided pattern workflow without JSON: patterns
(M9a), bindings and preview (M9b1), connections (this slice).

## 1. What the user can do

In the configuration tab, inside each net of a pattern:

1. **Add a connection**: _from_ and _to_ are chosen from that net's current
   members (they must differ), and the type is one of the five the composer
   accepts: wire, cable core, jumper, busbar, point sides (`internal`).
2. **Cable core** asks for the core number (a whole number from 1) and an
   optional core label. No other type has a core or a label; the cable itself
   is chosen in the binding (M9b1).
3. **Point sides** (`internal`) joins the inner and outer side of one strip
   point only.
4. **Edit** a connection in place (ends, type, core, label) or **remove** it.

Nothing is inferred: no connection, type, core or label is filled in, and no
connection is made automatically when members are added.

## 2. Storage and API (unchanged)

No new storage, schema, codec, renderer, migration or endpoint, and no server
change. A connection is the composer's existing shape,
`{from, to, via, core, label}`, in `patterns[].nets[].connections`, where
`from` and `to` are indexes into the net's `members`. The checks the editor
says before saving are the server's own (`codec_v2.parse_pattern` and
`compose.connect`); the server still decides, and a refused save creates no
revision.

| Problem                                | Said beside         | Server refusal      |
| -------------------------------------- | ------------------- | ------------------- |
| end not a member, or the same member   | the connection      | `pattern_invalid`   |
| unknown type                           | the connection      | `pattern_invalid`   |
| point sides across two points          | the connection      | `pattern_invalid`   |
| cable core without a core / core < 1   | the core field      | `pattern_invalid`   |
| core or label on another type          | the connection      | `pattern_invalid`   |
| cable core, binding names no cable     | the binding's cable | `missing_binding`   |
| cable not on the board                 | the binding's cable | `unknown_reference` |
| core beyond the cable's cores          | the binding         | `core_out_of_range` |
| one cable core used by two connections | the binding         | `core_reused`       |

The core-count check uses the cable's `cores` as the opened design states it;
where the design is not open it is not checked in the browser (the server
still checks it).

## 3. Reference integrity

Connections name members by position, and per-wire details
(`bindings[].wires`) name connections by position: the key `role/index` is the
connection's index in its net, and the composer derives the wire's and the
connection's ids from it. Before this slice, the editor inspects those keys:

- **Adding a member** appends it; every connection keeps both ends.
- **Removing a member** is refused while a connection names it; otherwise
  later ends are re-indexed so every connection still joins the same two
  members (unchanged from M9a). Wire details are keyed by connection, not
  member, so they are unaffected.
- **Adding a connection** appends it; earlier connections and their wire
  details keep their index.
- **Editing a connection** is refused while any binding holds wire details for
  it (they were entered for that connection as it is). The fields are shown
  disabled with the bindings and keys named.
- **Removing a connection** is refused while any binding holds wire details at
  its index or after it: removing it would drop its details and move each
  later connection's details onto another connection. The button says which
  bindings and keys.
- **Removing a net** is refused while any binding holds wire details for one
  of its connections.
- A wire-detail key that is not a plain index is kept untouched and counts as
  a use (it blocks removal).

No reordering is offered. There is no migration or discard policy: details are
never dropped, rekeyed or moved; the user removes them by JSON import if they
mean to, and then the change is allowed. An integration test shows why: with
the key kept and connection 0 removed, the server attaches the details to the
former connection 1.

## 4. Preview

The M9b1 preview (the read-only `configuration/validate`) is unchanged. It is
marked outdated after any configuration change, and its wording now covers a
newer saved revision as well as unsaved changes. A response for an older
request never replaces a newer one.

## 5. Unchanged

Renderer versions, snapshot schemas, codecs, composer rules, export hashes,
history, approval binding and review anchoring (a pattern whose content
changes is unreviewed by its definition digest, and a new revision holds no
approval), tenant isolation, execution export blocked, restricted formats
refused, JSON import. Unsupported imported fields elsewhere in a pattern are
kept as stored; the editor only writes the five connection keys the codec
accepts.
