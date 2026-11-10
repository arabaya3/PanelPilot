# M7a — Feeder link between two boards (contract)

Status: integration-owner contract, 2026-11-10. Builds on #214 (`51896d4`).
Draft; not for merge or deployment. Plan:
https://claude.ai/artifact/1APKQd2AV93UR1sB74irYm (sections 3–5, 7).

## 1. Scope

A saved, review-only record of the connection between a supply board and the
board it feeds, with both endpoints and the known cable/conductor properties.
It adds no engineering values, changes no calculation, and decides nothing
about completeness.

Out of scope: control circuits, life-safety, cable routes, sizing changes,
new export formats. Execution export and restricted formats stay refused.

## 2. Schema `pp-design/3`

- New snapshot schema `pp-design/3`, written only when the existing write
  switch `DESIGN_SNAPSHOT_SCHEMA` is `pp-design/3`. Default stays
  `pp-design/1`.
- v3 = the v2 envelope plus a top-level `links` collection (strictly sorted by
  id, canonical JSON like every other collection). Nets stay board-local;
  `board_mismatch` and the codec's net/connection rules are unchanged.
- v1 and v2 readers, codecs, hashes, renderer versions and fingerprints are
  unchanged. A stored v1/v2 revision opens and exports from its stored bytes
  under a v3 deployment, and its approval eligibility is unchanged.
- `RENDERER_VERSIONS["pp-design/3"]` is a new entry; v1/v2 entries untouched.
- A pure 2→3 read migration (`links: []`) may be used for display only.

## 3. The link

```
InterBoardLink {
  id                 "l-" + digest of (supply board, fed board); stable
  origin             "derived"            (one per server-owned feeder)
  feeder             { supply_board, fed_board, circuit_id }   from fed_from
  ends: [ LinkEnd(role "from", board = supply_board),
          LinkEnd(role "to",   board = fed_board) ]
  carrier            { kind: "cable" | "unknown", cable_id | null }  the feeder cable
  attributes         cores, cross_section_mm2, material, insulation,
                     length_m, number, colour
                     each { value, source: "user" | "unset" }   default unset
  provenance         { requirement_ref "<fed board ref>.feeder",
                       entered_by_user_id | null, entered_at | null }
}
LinkEnd {
  role, board,
  terminal: TerminalRef | null      a modelled terminal/strip point on THIS board
  label:    str | null              user's free-text designation (not a terminal)
  resolution: "resolved" (terminal set) | "unresolved" (terminal null)
}
```

- Exactly one link per server-owned feeder; `fed_from` stays the source of
  truth. The designer derives the link; the user never creates or deletes one.
- Designer values (feeder cable cores, material, insulation, cross-section,
  length) are NEVER copied into link attributes. A link attribute is `user` or
  `unset`.
- A label is printed as the user's text, never as a connected terminal.

## 4. Entry (request)

Project-level `feeder_links: [ { fed_board, from_end, to_end, attributes } ]`
in the request, all optional fields:

- `fed_board`: a board with `fed_from`; refused if unknown (422
  `link_board_unknown`) or not fed (422 `link_not_a_feeder`); twice → 422
  `link_duplicate`.
- `from_end` / `to_end`: `{ terminal?: TerminalRef, label?: str }`. A terminal
  not on that end's board (supply board for `from`, fed board for `to`) → 422
  `link_end_off_board`; a terminal that does not exist in the configuration →
  422 `link_terminal_unknown`.
- `attributes`: any subset; values validated by type/range only (cores int
  1–100, cross-section > 0 within existing bounds, length ≥ 0 within existing
  bounds, strings bounded and scanned by the life-safety scanner). Absent =
  unset. No defaults.
- Server sets provenance (who/when) from the saving account; client-sent
  provenance → 422.
- The request change goes through the normal save path (new revision, hashed,
  approval rules unchanged).

## 5. Findings

- `link_endpoint_unresolved` (per unresolved end) and `link_attribute_unset`
  (one per link listing unset attributes): warnings, block execution.
- `link_value_differs_from_calculation`: a user value differs from the value
  the feeder calculation used (cross-section, cores, material, insulation,
  length vs `feeder_length_m`). Shows both values and both sources. Does not
  change the calculation and does not block approval by itself. Wording never
  implies which is right.
- For v3 revisions, the open item `inter_board_wiring_not_modelled` is replaced
  per feeder by these findings only where a link exists; it is not removed in
  v1/v2. No finding says a link is "complete"; there is no completeness rule.

## 6. Output

- Wire schedule PDF gains a "Board-to-board links" section: link id, both
  boards, each end (terminal / "terminal NOT SET" / user label marked
  "label given by user — not a modelled terminal"), each attribute with source
  or "not given", and any discrepancy with both values.
- Connection drawing: a continuation flag at each modelled end whose far-board
  text comes from the link; an unresolved end is drawn as an open flag with
  "terminal NOT SET", never as a connected terminal.
- JSON export carries the links. Same revision, same snapshot hash, stamps
  unchanged. No CSV or interchange format.

## 7. Integrity and isolation

Links live inside the snapshot (and the request inside the requirements
record), so `snapshot_sha256`, the requirements hash and the approval binding
cover them unchanged. Links reference boards of the same project only.
