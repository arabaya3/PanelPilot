Status: M5a/M5b candidate, built on PR #211 (`ca034c1`) and the M5 proposal (`m5-routed-connections-proposal.md`). It describes what is built. It makes no claim of compliance or conformity with any electrical drawing standard, and none of engineering readiness. Every drawing stays review-only and NOT FOR EXECUTION; execution export stays blocked (`execution_package_incomplete`), and the restricted formats stay refused.

# M5: routed connection lines (first slice)

## 1. What is built

The connection drawing (`connection_drawing_pdf`) keeps every M4 cross-reference sheet, which stays the authoritative record of every row, end, wire value and open item. After them come **routed sheets**: per board, its components and strips as boxes, every terminal a dot, and every saved `Connection` drawn as exactly one line between its two terminal dots. Then the open-items, full-text, sheet-index and findings sheets, as before.

Generated from the saved snapshot only, through the same single read and the same `DrawingSet` as the schedules (`drawing_set(model).routed`). No device, family, configuration or customer is named in layout or renderer code (`test_the_held_out_configuration_is_data_only` now covers `connection_routing.py`).

Not built in this slice (the proposal's M5a schema hints): pin sides, symbol parts, strip anchors, placement or routing hints, net display hints. No schema change: every snapshot, digest and subject digest is unchanged. The layout below uses only what the model already holds; the hints would only refine it later.

## 2. Module boundary

| Module                                         | Owns                                                                                                                                                                                       |
| ---------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `app/design/model/connection_routing.py` (new) | `routed_set(model, drawing) -> RoutedSet`: placement, dots, fan-out, lanes, tracks, hops, labels, continuation flags, unrouted list and its open items. Pure, deterministic.               |
| `app/design/model/connection_drawing.py`       | unchanged interface; `DrawingSet.routed` holds the `RoutedSet`; `OPEN_KINDS` gains `unrouted`; junction points per block part; 8 mm stubs; each symbol's review status in its block header |
| `app/design/connection_drawing_pdf.py`         | draws the routed sheets exactly as laid out; legend and cover (counts, schematic statement)                                                                                                |

## 3. Layout rules (all fixed rules over data)

- **Placement.** Per board (a board never shares a routed sheet), in schedule order. Blocks are grouped by connected component of the board's own connections; a group that fits the current sheet stays together, else it starts a new sheet, else (larger than a sheet) it is placed block by block in breadth-first order of its connections and split by capacity (a block too tall continues in the other column or on the next sheet, "continued from / continues on sheet n, zone XY"). Components prefer the left column, strips the right; a full column continues in the other.
- **Boxes.** Title `<board>/<designation> (component|strip)`, always on one line (a name too long for the box is cut with `…[n]`, printed in full on the full-text list) (board-qualified so equal designations on different boards are not confused), the template, the symbol's review status (`symbol unreviewed: its drawing asserts nothing`, `symbol reviewed (template approved)`), or a dashed `symbol not defined` placeholder, and where the block's rows are on the cross-reference sheets. Each terminal row: its marking beside its dot, and a red `UNREALISED` / `UNCONNECTED` tag where the row has that note. Symbols themselves are drawn on the cross-reference sheets only, with their leads.
- **Dots.** A terminal point is a filled dot of 1.4 mm; a terminal with two or more connections (counted in the model) gets a filled junction dot of 2.2 mm, centred on the same point, replacing it. Dots sit 1.6 mm outside their box's edge, facing the channel. A dot that would fall within 1.5 mm of a zone row's edge is moved to 1.5 mm from it (its marking, centred on the dot, moves with it), so the dot and its marking sit in one zone; its lines then leave it by a short vertical piece.
- **Fan-out.** A terminal with k connections takes k rows of 4.2 mm; its lines leave the dot at points inside the dot (offsets of 0.35 mm), one per row: up to three up, three down and one straight (seven).
- **Lines.** Dot, fan-out, a horizontal escape (labelled), the connection's own vertical lane in the central channel, an escape, the other dot. Lanes: left-left pairs packed from the channel's left (shortest nearest), right-right from its right, left-right centred, one lane each, ordered by insertion so each adds the fewest crossings; two left-left or right-right runs share a lane only with 1.5 mm between them. Lanes are 3.5 mm apart. A sheet takes no more blocks than leave one lane for every connection touching it: a denser group continues on another sheet with flags instead of tighter lanes. Left and right rows sit on grids offset by half a row, so no two escapes are collinear.
- **Crossings.** Only a horizontal piece and a vertical piece of different connections cross, at a right angle; the horizontal one hops over the vertical one (a Bézier bump, 1.4 mm wide, leaving at least 2.1 mm between hops on adjacent lanes). No dot is ever drawn on a crossing; lines meet only inside a terminal's dot.
- **Continuations.** A far end on another sheet (another board, or a board split over sheets): the line runs down its lane to its own track in a band at the sheet's foot and on to a filled arrow flag at the frame edge (left-column lines to the left edge, right-column lines to the right), with `to <board>/<designation>:<marking> (sheet n, zone XY)` (and `, inner side` / `, outer side` after it for a strip point) above its end. The other sheet has the matching flag. A flag text too long for the band keeps `(sheet n, zone XY)` and the side whole: the far end's name is cut first (keeping its marking), then the marking too, with `…[n]`, and printed in full on the full-text list. Tracks are ordered so that tracks never cross lanes in the band.
- **Unrouted.** Beyond seven connections at one terminal, or when the channel has no free lane (the run with the longest span gives way first), the connection is not routed on any sheet: an arrowed 0.3 pt stub with `<label> not routed` where an end has a fan row (at an overcrowded terminal, one row per connection beyond the fan naming it, `w-… not routed`, and a row `(see the open items)`), its M4 stubs and cross-references unchanged, and an `unrouted` open item naming its label, both ends and the reason. The cover counts lines, continuations and unrouted (by reason).
- **Labels.** Above each escape: `w-` and a prefix of the wire id, or `x-` and a prefix of the connection id when there is no wire record; the shortest prefix of at least six characters no other connection or wire id of the model starts with.

## 4. PDF contract (what the independent read-back tests rely on)

- Terminal points: filled round dots under 3 mm (1.4 mm). Junction dots: filled round dots of 2.2 mm (1.57 times), centred on the terminal point, only at terminals with two or more connections, on every drawing sheet (cross-reference sheets too). A continued M4 row (`(cont.)`) keeps the plain dot.
- Routed lines: each connection is one stroked path (straight pieces and Bézier hops); its two ends lie inside its two endpoint dots, or one inside a dot and one at a continuation flag. Stroke width 0.35 pt, used for nothing else (symbols 0.5 pt, leads and stubs 0.3 pt, frames 0.6/0.3 pt). Jumper, internal and busbar lines (every via but wire and cable core, from the model's vocabulary) are dashed 5/2 pt; wire and cable-core lines solid.
- Crossings: orthogonal, with a hop; no dot within reach. Two unrelated collinear line ends are never within 3 mm.
- Continuation flag: a filled triangle 2 mm long at the frame edge, with the text `to <board>/<label> (sheet n, zone XY)` within 3 mm of the line's end.
- Line label: `w-…` or `x-…` (6 or more hex digits, a unique id prefix) 0.5 mm above its escape, within 1 mm of its line; it overlaps no other line or text.
- Block headers contain `(component)` or `(strip)`; the right column's header starts within 3 mm of its dots.
- Dotted stubs (dash `0.6 2.4`) and dash-dot stubs (`6 2 0.6 2`), each 8 mm, on the cross-reference sheets.
- Every routed sheet's top line starts `ROUTED CONNECTION SHEET: line paths are schematic`.

## 5. Readability (a product target at A3, not a standard)

Routed sheets: 7 pt for markings, labels, continuation texts and notes, 6 pt for the grey template/review lines, 8 pt titles. Cross-reference sheets: 6 pt body, and (since the print-size review) 6 pt for the grey wire and net detail lines too; pin markings and a symbol's own text at a symbol stay at 5 pt. This is the product readability target. At A3, 7 pt Helvetica has a cap height of about 1.8 mm; labels sit in 4.2 mm rows. The read-back tests keep their 6/5 pt parameters, which both kinds of sheet meet.

## 6. Consistency and determinism

`drawing_set` computes the routed set from the same model and `DrawingSet`, so its rows, ends and labels are the schedules'. Every connection id is in exactly one of `routed`, `continued` (one flagged line on each of its two sheets) or `unrouted`. All orders are by schedule index or id and all limits are counts. The v2 renderer version is `2026.11.01.v2-7`; `drawing_routed_crowded` joins the v2 fingerprint cases.

M4 tests changed, with reasons: the geometry tests' lead, lead-reader and zone-reference checks apply to cross-reference sheets only (`Page.is_cross_reference`): a routed sheet has no symbol leads and its flags are checked by `test_routed_topology.py`. The open-item oracle accepts `unrouted` items, which are a routing outcome rather than a model fact, and checks them against `DrawingSet.routed`. The point column moved 5 mm right (`POINT` 41 mm, leads still end at 32 mm) to make room for the 8 mm stubs.

## 7. Test data

`drawing_routed_crowded` (added as data only: the JSON and its findings fingerprint entry): eight components and a 34-point strip on one board; crossing lines; a strip point joined three ways (junction); a group larger than one routed sheet, so lines continue across sheets; a common terminal with nine connections, two of them unrouted.

## 8. Decisions for the owner and a qualified engineer

M5-1 (lines acceptable, with the schematic wording), M5-3 (junction dots, hop crossings, dashed links: neutral, no standard claimed), M5-9 (one board per routed sheet run, lines continue across sheets by flags), M5-11 (budgets: seven per terminal, the channel's lanes) and M5-12 (unrouted is informational) are taken at the proposal's defaults and remain open.

## 9. Notes on decisions taken

- Continuation text for a strip point: the label is `<board>/<designation>:<marking>` without the side and level (the PDF contract's label has none); the side follows the parenthesis, `to XR:8 (sheet 4, zone 7E), inner side`.
- The `symbol reviewed (template approved)` rendering is untested by data: a submitted configuration may not claim a review, so no fixture has an approved template.
