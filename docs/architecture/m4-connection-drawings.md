Status: M4 foundation, built on the M3 candidate (`da73b23`). It describes what M4 builds and what it does not. This document makes no claim of compliance, conformity with a standard, or engineering readiness. Nothing in M4 makes a design ready for execution.

# M4: review-only connection drawings from the saved model

## 1. Scope

M4 adds one review export, `connection_drawing_pdf`: a paginated drawing of a saved `pp-design/2` revision's components, terminal strips, terminals, nets, connections and wires. It is generated from the revision's stored snapshot only, and is marked review-only and NOT FOR EXECUTION on every sheet, like the terminal and wire schedules.

M4 also adds optional **symbol data** on a component template (`symbol`), so the drawing's symbols come from data. A template without one is drawn as a labelled placeholder ("symbol not defined").

Out of scope: any engineering content (ratings, cross-sections, colours, numbering, standards), routing optimisation, a schematic of the v1 power design (the existing drawing set does that), editing, DXF or ECAD output of the connection drawing, and any execution-readiness gate (M7).

## 2. Module boundary

| Module                                   | Owns                                                                                                                                                                                               | Must not                                                                      |
| ---------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------- |
| `app/design/model/entities.py`           | `SymbolDef` (shapes and terminal anchors, integers in a declared grid) and `ComponentTemplate.symbol` (`None` when absent)                                                                         | hold drawing code                                                             |
| `app/design/model/codec_v2.py`           | parsing and dumping `symbol`, strictly; the key is written only when present, so every existing template's canonical JSON, `registry_lock` digest and review subject digest is unchanged           | default or infer a symbol                                                     |
| `app/design/model/connection_drawing.py` | the pure, framework-free **layout**: `drawing_set(model) -> DrawingSet`. Blocks, rows, sheets, zones, cross-references and open items, all derived from the model and from the schedules' own rows | render, read the database, take client input, know any device or panel family |
| `app/design/connection_drawing_pdf.py`   | **rendering** a `DrawingSet` to PDF: frame, zone grid, title block, stamps, symbols from `SymbolDef`, placeholders, legend, open items and findings sheets                                         | decide what is on a sheet, compute or look up any value                       |
| `app/domain/design_projects.py`          | gating and the single snapshot read: the drawing is produced in `_schedule_export`, from the same `VersionedModel` (`_check_snapshot`, anchored) the schedules use                                 |                                                                               |

The drawing code is owned by the M4 drawing work. Consistency tests (written separately) use only the public interface of Section 3, never the PDF renderer's internals.

## 3. Interface for consistency tests

`connection_drawing.drawing_set(model: DesignModelV2) -> DrawingSet` is deterministic and pure. A `DrawingSet` holds:

- `terminal_rows`: exactly `schedules.terminal_schedule(model)` (the same tuple, in the same order). Every drawn terminal row is one of them.
- `wire_rows`: exactly `schedules.wire_schedule(model)`.
- `rows`: one `Row` per terminal row, in the same order: its `terminal` (the `TerminalRow` itself), `key` (`TerminalRef.key`), `label`, `place` (`Place`: sheet, column, slot, and `zone` such as `2B`), `notes` (unconnected, unrealised) and `ends`. Each `End` is one connection at that terminal: `connection_id`, `via`, `net_id`, `signal`, `far_key`, `far_label`, `far_place`, the `WireRow` when there is a wire, `wire_missing`, and the cable `core`.
- `sheets`: drawing sheets (`Sheet`: `number`, `board_id`, `parts`); each `BlockPart` is the part of one component's or strip's block on that sheet, with its `lines` (each a row's note or end), `symbol`, and `continued_from` / `continues_on`.
- `places`: the sheet and zone of every terminal, by `TerminalRef.key`.
- `open_items`: `OpenItem(kind, board_id, subject, text, place)`, kinds in `OPEN_KINDS`: an unrealised net (subject the net id), an unconnected terminal (required, requiredness unresolved, documented not required, strip point; subject the row label), a wire record missing (connection id), an unset or assumed wire attribute (wire id), a designation not set, a symbol not defined, a template with no terminals (component or strip id).
- `connections`, `nets`: every connection and net id of the model.
- Text helpers used for every printed value: `cell_text`, `wire_text`, `end_texts`, `row_marking`, `row_net`; `NOT_SET` is `"not set"`.
- Helpers `terminal_keys()`, `net_ids()`, `wire_ids()`, `connection_ids()` for set comparison with the schedules and the model.

Invariants a consistency test can hold the layout to:

1. `[row.terminal for row in rows] == terminal_rows`, `terminal_keys()` is the schedule's terminals, `wire_ids() == {wire.id for wire in model.wires}`, `net_ids() == {net.id for net in model.nets}`, `connection_ids() == {connection.id for connection in model.connections}`.
2. Every connection is drawn at both ends; each end names the other end's label, sheet and zone, and a wire end carries its `WireRow` (number, cross-section and colour cells with sources).
3. Every terminal in no net is drawn with an `UNCONNECTED`/`unconnected` note and listed as an open item; every member of a net its connections do not join carries an `UNREALISED` note and the net is listed.
4. Every sheet's text holds the export's label, `CONNECTION DRAWING — REVIEW ONLY — NOT FOR EXECUTION`, `Snapshot SHA-256: <hash>` and `Sheet n of N`.

`model/test_connection_drawing.py` and `test_connection_drawing_pdf.py` (DB-free, every configuration) and `test_the_connection_drawing_and_schedules_of_a_revision_agree` (DB, through the export route) hold these today. 4. The PDF carries the snapshot hash (`DrawingHeader.design_sha256`) on every sheet's title block.

The export path is one function call: `design_projects._schedule_export(project, row, read, format, mark, applicable)` for all three formats, with `read` from one `_check_snapshot` call.

## 4. What the drawing shows, and how

- **Sheets.** A3 landscape. Sheet 1 is the cover: project, revision, full snapshot hash, schema, rules and renderer version, legend, sheet index. Drawing sheets follow, one board at a time (a board never shares a sheet). Then the open-items sheets, the sheet index and the findings sheets (structural findings with their effects, then the design's other findings). Every sheet: frame, zone grid (columns `1..8`, rows `A..F`), title block (project, revision, sheet `n of N`, board, hash, schema, renderer version), the export's mark top and foot (as the schedules) and `CONNECTION DRAWING — REVIEW ONLY — NOT FOR EXECUTION` in the same red (`#b00020`, bold) as the schedules' label.
- **Blocks.** Components first, then strips, in the terminal schedule's order. A block is the symbol (or the placeholder) and one row per terminal row; a block too tall for the space left is continued in the next column or sheet, saying "continued from / on".
- **Symbols.** From the template's `symbol` (Section 4a), scaled into the block. Each anchor joins the terminal it names to its row; a terminal with no anchor is joined to the symbol's edge by a dotted lead, never guessed onto a pin. A strip's symbol is drawn without anchors (its rows are points, not template terminals). A template with no symbol, or a strip with no template, is drawn as a dashed box labelled `symbol not defined` and listed as an open item.
- **Notes.** Every row says something: its ends, or why it has none (`UNCONNECTED`, `UNREALISED`, or `in net <signal> alone: no connection`). A component whose template defines no terminals (every legacy v1 device) is drawn as a header and placeholder and listed.
- **Connections.** Each connection is drawn as a stub at each of its ends, labelled with the far end's label (a strip point with its side and level), where it is (`sheet 4, zone 6B`, or `this sheet, zone 2A`) and its `via`. Lines are not routed across the sheet: cross-references are always written, so nothing depends on following a line.
- **Wires.** A wire's stub names its id and its number, cross-section and colour with their source; a value the model does not hold prints `not set`, an assumed one prints the value with `assumed (<assumption id>)`. Nothing is filled in.
- **Open items, shown where they are and listed.** On the row: `UNCONNECTED: required (documented)`, `UNCONNECTED: requiredness unresolved`, `unconnected: not required (documented)`, `UNCONNECTED: this point side is in no net`; `UNREALISED: net <signal> (<id>) is not joined by its connections` for every member of such a net; a `via: wire` connection with no wire record as `WIRE RECORD MISSING`; a missing designation as `designation not set`. All of them are listed again on the open-items sheets.
- **Never invented.** Markings, designations, signals, wire attributes, requiredness and review status are printed only as the snapshot holds them; otherwise `not set` or `unresolved`. No standard is named, and the cover says that nothing on the drawing states or checks a rating, size, colour, numbering convention or standard.

## 4a. Symbol data

A template may carry `symbol: {width, height, shapes, anchors}`: whole numbers in a grid of at most 100 by 100, y downwards; `shapes` (at most 64) are `line` (x1 y1 x2 y2), `rect` (x y width height), `circle` (cx cy r) and `text` (x y, with its `text`), sorted by kind, points and text; `anchors` name a terminal of the template and a point in the grid, sorted by terminal. Anything else is refused when the snapshot is read (`codec_v2._symbol`), including an anchor naming no terminal of the template. The key is written only when present, so every template without one keeps its canonical JSON, `registry_lock` digest and review subject digest; a symbol is part of both digests, so a review decision covers it. Configurations count shapes and anchors in the template size limit.

## 5. Gating

`connection_drawing_pdf` is added to `SCHEDULE_FORMATS`, so it follows exactly the schedules' path: refused for a draft and for a `pp-design/1` revision (`schedule_requires_v2`), and for a revision through the same integrity, stale, approval-binding and plan path; `purpose: execution` is refused (`execution_package_incomplete`) before anything else; the restricted formats (`UNPROTECTED_FORMATS`) are unchanged and stay refused. It is protected (not in `UNPROTECTED_FORMATS`): the mark is printed on every sheet.

`DESIGN_SNAPSHOT_SCHEMA` still defaults to `pp-design/1`; the drawing exists only for revisions saved as `pp-design/2`.

## 6. Renderer version

The v2 renderer version is bumped, and the drawing of each fingerprint case is recorded in `renderer_fingerprints_v2.json`, held by `test_renderer_version.py` like the schedules: content changed without a version bump fails.

## 7. Test data

Every synthetic configuration in `app/tests/design/model/configurations/` is drawn (seven). Two are added by M4, both with symbol data:

- `drawing_control_relay`: symbols, a template without one, an undocumented template, an unrealised net, user and assumed wire values, a component without a designation.
- `drawing_heldout_feeder`: added after the renderer, as data only. Its commit ("Add a held-out connection drawing case as data only") adds the JSON configuration and its new entry in `findings_fingerprints.json` (an addition; no existing entry changes) and nothing else. It has its own templates and symbols, and a 30-point strip that continues across columns and sheets, with a wire crossing sheets. `test_the_held_out_configuration_is_data_only` checks that the layout and renderer name no configuration, template, pattern, designation or device word.

## 8. Open engineering decisions

| #   | Decision                                                                                        | Status                                                                                    |
| --- | ----------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------- |
| D-1 | Which drawing standard, symbol library and zone convention a customer-facing drawing follows    | Open (QE). M4's symbols are data only, its zones a plain grid, and no standard is claimed |
| D-2 | Whether connections are routed as lines on a sheet, or only cross-referenced                    | M4 cross-references only; routing is open                                                 |
| D-3 | How cross-references are written (sheet/zone format)                                            | M4: `sheet n, zone 3B`; open                                                              |
| D-4 | Who authors and reviews symbol data, and whether it is reviewed with the template or on its own | M4: part of the template's digest, so a template review covers it; open (owner)           |
| D-5 | Whether a drawing language other than English is needed for this drawing                        | M4: English only, like the schedules                                                      |
| D-6 | Wire numbering, cross-sections, colours, terminal ratings (M3 O-3 to O-6)                       | Unchanged: printed only as the snapshot holds them                                        |
| D-7 | Whether a strip's symbol may anchor to point positions, and how a strip template defines that   | M4: a strip symbol is drawn without anchors; open                                         |
| D-8 | Whether every legacy v1 device (no terminals) belongs on a connection drawing                   | M4: drawn as a header with a placeholder and listed, never hidden; open                   |
| D-9 | Sheet size, and whether boards may share a sheet                                                | M4: A3 landscape, one board per sheet run; open                                           |
