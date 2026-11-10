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

## 8. As built (backend, branch `m7a-api`)

Shapes are as §3–§6, with these specifics and deviations:

- **Envelope**: `{"schema": "pp-design/3", "model": {<every v2 key>, "links": [...]}}`.
  The v2 part is read by the unchanged v2 codec; `links` by `codec_v3`, which
  requires exactly one link per feeder of the design (none extra), the derived
  id `l-<24 hex>` = `ids.derive("l", supply_board, fed_board)`, canonical JSON
  and strict id order. A rehashed link that is not the derived one (e.g. an
  attribute with source `data`) reads as unreadable → `snapshot_integrity_failed`.
- **Boards are named by board id** in `feeder`, `ends[].board` (in this
  designer a board's id is its name). Request `fed_board` is the board name.
- **Attribute JSON**: `cores` is an int; `cross_section_mm2` and `length_m` are
  canonical decimal strings (`"16"`, not `"16.00"`); others strings. The
  request accepts decimals as strings or numbers. `attributes` is an object
  with all seven keys, each `{value, source}`.
- **TerminalRef** (request and stored): `{component, terminal}` or
  `{point, side}`. From `model_v2.terminals`: owner_kind `component` →
  `{component: owner_id, terminal: terminal_id}`; a strip point →
  `{point: terminal_id, side}`; filter by `board_id`.
- **Provenance**: `entered_by_user_id` (the saving account) and `entered_at`
  (save time, ISO 8601 UTC, seconds) are set only when the link holds a
  user-entered value (terminal, label or attribute); otherwise both `null`.
  Not carried forward from an earlier revision: each save records its saver.
  Client-sent `provenance` → 422 (unknown field, pydantic).
- **Refusals** (422, `params`: `fed_board`, `board`, `link_index`; terminal
  ones also `end`, `end_board`, and `terminal_board` for off-board):
  `link_board_unknown`, `link_not_a_feeder`, `link_duplicate`,
  `link_end_off_board`, `link_terminal_unknown`. Terminals are checked against
  the model saved in the same request (with no configuration no terminal is
  modelled, so any terminal is `link_terminal_unknown`). Added:
  **409 `feeder_links_require_v3`** when `feeder_links` is sent and the switch
  is not `pp-design/3` (params `written_schema`, `required_schema`). A
  configuration is also accepted under v3. If the design itself is refused,
  the revision is saved as before with no snapshot and no links.
- **Request**: `feeder_links` is omitted from the stored request when not
  sent (`exclude_if`), so v1/v2 requests store byte for byte as before.
- **Findings** (source `integrity`, in `project.findings`, `values` are
  strings): `link_endpoint_unresolved` (warning, confirm; `link_id`, `end`,
  `end_board`, `board_id`, `fed_board`), `link_attribute_unset` (warning,
  confirm; `attributes` is a **comma-separated string**, not a list, since
  finding values are strings), `link_value_differs_from_calculation` (warning,
  no confirmation, never blocks approval; `attribute`, `user_value`,
  `user_source: user`, `calculation_value`, `calculation_source: feeder
  calculation`). The first two are execution blockers. Text values compare
  case- and whitespace-insensitively. Calculation values: the feeder circuit's
  cable (supply board circuit with `feeds` = fed board): cores, cross-section,
  material, insulation, `length_m` (= `feeder_length_m`); a missing length is
  not compared.
- **Open item**: in v3 records `inter_board_wiring_not_modelled` is left out
  for each feeder that has a link (every feeder in v3); v1/v2 unchanged.
- **Open revision**: `SavedProject.links` (InterBoardLink shape, `null` for
  v1/v2, stale, or unreadable) and new `SavedProject.written_schema` (the
  deployment's switch). No `model_v3`; the v2 summary stays `model_v2`.
- **Life safety**: link labels and material/insulation/number/colour are
  scanned as request text (place `link`, where `feeder link to <board> ›
  <field>`); a life-safety term holds approval and cannot be excluded or
  marked false positive while in the request.
- **Drawing**: flags are drawn on dedicated "Board-to-board links" sheets after
  the routed sheets (not inside the existing cross-reference sheets, whose
  layout is unchanged): a solid flag with a dot for a resolved end (with the
  sheet/zone where that terminal is drawn), a dashed open red flag reading
  "terminal NOT SET" for an unresolved one; far-board text from the link.
- **Renderer**: `RENDERER_VERSIONS["pp-design/3"] = "2026.11.10.v3-1"`; v1
  `2026.11.04.9` and v2 `2026.11.04.v2-24` and their fingerprint files are
  untouched; v3 fingerprints in `renderer_fingerprints_v3.json`.
- **2→3 migration** (`links: []`) is display-only and never parsed as v3.
