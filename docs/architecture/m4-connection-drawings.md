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
- `sheets`: drawing sheets, each with its number, board and placed blocks; each block has its rows, each row its terminal (`TerminalRow`), sheet and zone, and its connection ends (`End`) with the far end's label, sheet and zone.
- `places`: the sheet and zone of every terminal, by `TerminalRef.key`.
- `open_items`: every unrealised net group, unconnected terminal (required, unresolved or not required), unset or assumed wire attribute, and other unresolved value, each with its kind and text.
- Helpers `terminal_keys()`, `net_ids()`, `wire_ids()`, `connection_ids()` for set comparison with the schedules and the model.

Invariants a consistency test can hold the layout to:

1. `{row key} == terminal schedule keys`, `wire_ids() == {wire.id for wire in model.wires}`, `net_ids() == {net.id for net in model.nets}`, `connection_ids() == {connection.id for connection in model.connections}`.
2. Every connection is drawn at both ends; each end names the other end's label, sheet and zone, and a wire end carries its `WireRow` (number, cross-section and colour cells with sources).
3. Every terminal in no net is drawn and listed as an open item.
4. The PDF carries the snapshot hash (`DrawingHeader.design_sha256`) on every sheet's title block.

The export path is one function call: `design_projects._schedule_export(project, row, read, format, mark, applicable)` for all three formats, with `read` from one `_check_snapshot` call.

## 4. What the drawing shows, and how

- **Sheets.** A3 landscape. Sheet 1 is the cover: project, revision, full snapshot hash, schema, rules and renderer version, legend, sheet index. Drawing sheets follow, one board at a time (a board never shares a sheet). Then the open-items sheets, then the findings sheets. Every sheet: frame, zone grid (columns `1..8`, rows `A..F`), title block (project, revision, sheet `n of N`, board, hash, schema, renderer version), the export's mark top and foot (as the schedules) and `CONNECTION DRAWING — REVIEW ONLY — NOT FOR EXECUTION` in the same red (`#b00020`, bold) as the schedules' label.
- **Blocks.** Components first, then strips, in the terminal schedule's order. A block is the symbol (or the placeholder) and one row per terminal row; a block too tall for the space left is continued in the next column or sheet, saying "continued from / on".
- **Symbols.** From the template's `symbol`, scaled into the block. Each anchor joins the terminal it names to its row. A template with no symbol, a strip with no template, or a symbol anchor naming no terminal of the template is drawn as a dashed box labelled `symbol not defined` (or the anchor listed as an open item).
- **Connections.** Each connection is drawn as a stub at each of its ends, labelled with its `via`, the far end's label and `sheet n / zone` (`this sheet` when on the same sheet). Lines are not routed across the sheet: cross-references are always written, so nothing depends on following a line.
- **Wires.** A wire's stub names its id and its number, cross-section and colour with their source; a value the model does not hold prints `not set`, an assumed one prints the value with `assumed (<assumption id>)`. Nothing is filled in.
- **Open items, shown where they are and listed.** On the row: `UNCONNECTED — required`, `UNCONNECTED — requiredness unresolved`, `unconnected — not required (documented)`; `UNREALISED in net <signal>` for a member its net's connections do not reach; a `via: wire` connection with no wire record as `wire record missing`; a missing designation as `designation not set`. All of them are listed again on the open-items sheets.
- **Never invented.** Markings, designations, signals, wire attributes, requiredness and review status are printed only as the snapshot holds them; otherwise `not set` or `unresolved`. No standard is named, and the cover says that nothing on the drawing states or checks a rating, size, colour, numbering convention or standard.

## 5. Gating

`connection_drawing_pdf` is added to `SCHEDULE_FORMATS`, so it follows exactly the schedules' path: refused for a draft and for a `pp-design/1` revision (`schedule_requires_v2`), and for a revision through the same integrity, stale, approval-binding and plan path; `purpose: execution` is refused (`execution_package_incomplete`) before anything else; the restricted formats (`UNPROTECTED_FORMATS`) are unchanged and stay refused. It is protected (not in `UNPROTECTED_FORMATS`): the mark is printed on every sheet.

`DESIGN_SNAPSHOT_SCHEMA` still defaults to `pp-design/1`; the drawing exists only for revisions saved as `pp-design/2`.

## 6. Renderer version

The v2 renderer version is bumped, and the drawing of each fingerprint case is recorded in `renderer_fingerprints_v2.json`, held by `test_renderer_version.py` like the schedules: content changed without a version bump fails.

## 7. Test data

Two or more synthetic configurations are drawn. One, `drawing_heldout_symbols`, is added as a JSON configuration only, with symbol data, and no renderer change: `test_connection_drawing.py` draws every configuration in `configurations/`, and the commit adding it touches only that file.

## 8. Open engineering decisions

| #   | Decision                                                                                        | Status                                                                                    |
| --- | ----------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------- |
| D-1 | Which drawing standard, symbol library and zone convention a customer-facing drawing follows    | Open (QE). M4's symbols are data only, its zones a plain grid, and no standard is claimed |
| D-2 | Whether connections are routed as lines on a sheet, or only cross-referenced                    | M4 cross-references only; routing is open                                                 |
| D-3 | How cross-references are written (sheet/zone format)                                            | M4: `sheet n / zone 3B`; open                                                             |
| D-4 | Who authors and reviews symbol data, and whether it is reviewed with the template or on its own | M4: part of the template's digest, so a template review covers it; open (owner)           |
| D-5 | Whether a drawing language other than English is needed for this drawing                        | M4: English only, like the schedules                                                      |
| D-6 | Wire numbering, cross-sections, colours, terminal ratings (M3 O-3 to O-6)                       | Unchanged: printed only as the snapshot holds them                                        |
