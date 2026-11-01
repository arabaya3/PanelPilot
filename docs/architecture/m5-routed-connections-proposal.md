Status: proposal only, based on the M4 candidate (`ef38bed`, PR #211). Nothing here is built. This document makes no claim of compliance or conformity with any electrical drawing standard, and none of engineering readiness. Which standard a drawing follows is an owner and qualified-engineer decision (M4 D-1, M3 O-2). Every drawing it describes stays review-only and NOT FOR EXECUTION; execution export stays blocked (`execution_package_incomplete`, M3 O-12).

# M5 proposal: routed connection lines and richer symbol data

## 1. Goals and non-goals

M4 draws each connection as two labelled stubs with cross-references (`sheet 4, zone 6B`). On the samples (`drawing_control_relay`, `drawing_heldout_feeder`) that is complete but hard to read: a reviewer follows text, not lines, and a 30-point strip spreads over several sheets with no picture of what joins what. M5 would add drawn lines where the layout can draw them honestly, and keep the cross-reference everywhere else.

Goals:

- G-1. Draw a connection as an orthogonal line between its two ends when both ends are on the same sheet and a route is found; otherwise keep M4's stub and cross-reference, unchanged.
- G-2. Every input to the layout is data in the snapshot: template symbol geometry and pin sides, optional placement and routing hints. No device, function, panel family or customer configuration is named in layout or renderer code.
- G-3. Deterministic: the same snapshot and renderer version give byte-identical layout and a stable fingerprint.
- G-4. Unknowns stay explicit on the sheet and in the open items: unrouted, unplaced, symbol missing, ambiguous.
- G-5. Additive, versioned schema changes; historical revisions and their digests are never rewritten.

Non-goals:

- Any claim that a line shows the physical wire path, duct, length, bundle or terminal order in the enclosure.
- Inventing terminal markings, ratings, wire sizes, colours, wire numbers or potentials. A bus or jumper is drawn only where the model holds a net and connections that say so.
- A shipped symbol library, or symbols "per standard". Symbols remain data a tenant or operator supplies and reviews.
- Editing on the drawing, DXF/ECAD export of the routed drawing, and execution readiness (M7).
- Replacing the schedules. The schedules remain the reference; the drawing is a view of the same rows.

## 2. Data model additions

All additions are optional keys on existing `pp-design/2` objects, written only when present (the M4 `symbol` rule), so every existing object keeps its canonical JSON, `registry_lock` digest and M3 subject digest. Each is parsed strictly in `codec_v2` and refused when malformed; nothing is defaulted or inferred. A model that uses none of them draws exactly as M4 (that is a test, Section 5).

| Addition | Lives in | Content (summary) | Reviewed by | Digest / version impact |
| --- | --- | --- | --- | --- |
| A. Symbol pin sides | template `symbol` | per anchor: `side` (`left\|right\|top\|bottom`) and optional `order`; must agree with the anchor's edge point | M3 template decision (part of subject digest) | Template digest changes only for templates that add it; earlier decisions stop applying to the new digest, as M3 intends |
| B. Symbol variants / multi-part | template `symbol` | optional `parts[]`, each a `SymbolDef` with its own anchors, so a device whose terminals sit in separate groups can be drawn as parts (each part names the terminals it carries; a terminal is in at most one part) | M3 template decision | As A |
| C. Strip point anchors (D-7) | strip template `symbol` | `point_pitch` and `side` per strip side (`inner`, `outer`), so a strip symbol repeats per point; no per-point invented marking | M3 template decision | As A |
| D. Placement hints | design: component / strip | optional `layout: {group, order, sheet_hint}`, opaque strings/ints from the user | Not an engineering subject; recorded in the revision snapshot like any edit | Changes the snapshot hash only for revisions that set it |
| E. Potential / bus | design: net | optional `display: {as: "bus" \| "lines", label}`; `bus` is a drawing request only. The bus is drawn only through connections the model holds | Design data; a finding if `bus` is asked for a net whose connections do not form a chain or tree | As D |
| F. Jumpers | none new | existing `via: jumper` / `busbar` (M2 `VIAS`) are drawn as short bridges between adjacent ends when adjacent; otherwise as ordinary routed lines labelled with their `via` | Unchanged (M3 O-7 open) | None |
| G. Cable and core grouping | none new | existing `CableCore {cable_id, index, label}` groups lines that share a cable into one labelled bundle segment where they run together; cores print only `label`/`index` as held | Unchanged | None |
| H. Routing hints | design: connection | optional `layout: {route: "line" \| "reference"}`; `reference` forces M4's stub. No coordinates are stored in M5 | Design data | As D |

Rules that apply to all of them:

- A hint can only make the drawing less ambitious or reorder it. It never adds a connection, a net member, a marking or an attribute, and never hides an open item.
- Template-side additions (A, B, C) are geometry and are covered by the template's review decision like M4 symbols (D-4). Design-side hints (D, E, H) are not engineering content and are not routed through M3 decisions; whether they should be is decision M5-4.
- Schema: a new minor marker in the v2 envelope is not needed because the keys are optional; the reader refuses unknown keys today, so an old reader refuses a new snapshot rather than silently ignoring hints. A revision saved before M5 is read and drawn by the M4 path. `RENDERER_VERSIONS["pp-design/2"]` is bumped; a re-export of an old revision under the new renderer records the new renderer version, as now. The old revision's snapshot is never rewritten.
- Size limits: anchors, parts and hints count against the existing template and configuration size limits.

## 3. Layout pipeline

A new pure module (proposed `model/connection_routing.py`) sits after `drawing_set` and before the renderer. Input: `DesignModelV2` and the M4 `DrawingSet`. Output: a `RoutedSheet` per drawing sheet with placements, ports, segments, buses, and a list of unrouted connections. The renderer only draws it. `DrawingSet`'s M4 interface is unchanged, so the consistency tests keep holding.

Stages:

1. **Partition.** Per board, as M4. Connected components of the board's connection graph (ignoring `route: reference`) are the unit of sheet assignment; a component that does not fit a sheet is split at the fewest cut connections (greedy by order, bounded), and every cut connection becomes a cross-reference.
2. **Placement.** Blocks (component parts, strips) placed on a coarse grid of cells. Order: placement hints first, then the schedule order as tie-break.
3. **Ports.** Each anchor becomes a port with a side and an offset from the symbol data (A). Without a side, the port side is taken from which edge the anchor lies on (M4 already requires edge points); a corner point is ambiguous and the connection is not routed (`ambiguous_pin_side`). Templates without symbols get one port column on the right, as M4's rows, marked as placeholder ports.
4. **Routing.** Orthogonal routes between ports in channels between blocks (below).
5. **Buses.** A net with `display: bus` gets one trunk segment in a channel, with taps to each member; only members joined by the net's connections are tapped; an unrealised member keeps its `UNREALISED` note and gets no tap.
6. **Crossing reduction and nudging.** Order tracks within each channel to reduce crossings; separate parallel segments by a minimum pitch; mark each unavoidable crossing as a plain crossing (no dot). A junction dot is drawn only where the model has a node (a terminal or a bus tap).
7. **Labels.** Each line keeps M4's end texts at both ends (far end, `via`, wire cells). Cross-references stay printed even on a routed line (decision M5-2).

Candidate algorithms:

| Stage | Option | For | Against |
| --- | --- | --- | --- |
| Placement | Layered (Sugiyama-style: layer assignment, barycentre ordering, coordinate assignment) | Proven, deterministic, good for flow-like control circuits; crossing reduction built in | Assumes a direction; dense meshes and strips with many points give wide layers; needs cycle breaking |
| Placement | Fixed columns in schedule order (M4) + local reordering | Simple, predictable, stable across revisions (small edit, small drawing change) | More crossings; long lines |
| Placement | Force-directed / annealing | Good looking on small graphs | Non-deterministic unless seeded, unstable across edits, hard to bound in time. Not recommended |
| Routing | Grid / maze (Lee, A* on an occupancy grid) | Handles any obstacle shape; finds a route when one exists on the grid | Cost grows with grid size × nets; net order changes results; needs rip-up for quality |
| Routing | Channel routing (left-edge / dogleg in channels between block columns) | Fast, predictable track counts, clean parallel lines; fits the column layout | Needs a channel structure; fails gracefully only if the channel width is fixed beforehand |
| Routing | Orthogonal visibility graph + shortest path (as in libavoid-style connectors) | Fewer bends, good quality | More complex; more code to keep deterministic |

Recommendation for the first slices: fixed columns with local barycentre reordering (stable across revisions, which matters for reviewing a diff between revisions), channel routing between columns, and A* on a coarse grid only as a fallback for the few connections a channel cannot take. A layered placement is a later option behind a renderer version bump, evaluated against the same fixtures.

**Determinism and fingerprints.** All coordinates integers on the layout grid; every iteration order is by id; ties broken by id; no randomness, no time, no hash-set ordering. The layout gets its own canonical JSON and SHA-256 (`layout_sha256`), printed in the title block beside the snapshot hash and the renderer version, and recorded in `renderer_fingerprints_v2.json` per fingerprint case.

**Scale limits and timeouts.** Limits are counted, not timed, so they are deterministic: maximum blocks and connections per sheet, maximum grid cells expanded per route, maximum rip-up passes. A connection that exceeds a budget is left unrouted (Section 4), never truncated mid-line. A wall-clock timeout on the export protects the server only; if it fires the export fails with an error, it does not emit a partly routed drawing, because a result that depends on time would not be reproducible. Initial budgets to be set from the fixtures plus a synthetic stress configuration (for example 500 terminals on a board).

## 4. How unknowns stay explicit

New `OPEN_KINDS`, each printed at the place it occurs and listed on the open-items sheets:

- `unrouted`: no line drawn (budget, no channel room, split across sheets, `route: reference`, or an end without a port). The stub and cross-reference are drawn exactly as M4, with `not routed: <reason>`. A connection cut by sheet splitting is listed with reason `other sheet`, as information rather than a defect.
- `unplaced`: a block the placement could not fit; drawn in an overflow area/sheet with its rows and stubs, never dropped.
- `symbol not defined` (M4, unchanged) and `pin side unknown`: a placeholder or edge-ambiguous anchor; dotted lead, no guessed side.
- `ambiguous`: conflicting hints (two `order` values equal in a group, a bus requested for a net that does not form a chain), or a terminal in two symbol parts (refused at read time instead, if decided so).

Invariant: every connection is either drawn as a line between both its ends or listed as unrouted with both stubs present. Nothing is silently omitted. The cover sheet gives counts: connections routed, unrouted (by reason), blocks unplaced.

## 5. Consistency, testing and review gates

Consistency: the routed layout consumes `DrawingSet`, so M4's invariants (rows equal the terminal schedule, every connection at both ends, open items) are unchanged and still tested. New invariants:

1. Every routed line's endpoints are the ports of its connection's two `TerminalRef`s; every connection id appears exactly once as routed or unrouted.
2. Lines do not pass through a block's box, do not overlap another line collinearly, and do not touch another line's port; a junction appears only at a model node.
3. A bus taps exactly the members its connections join.
4. No printed text other than M4's helpers' output; no value absent from the snapshot.
5. With no hints and no pin sides, the result equals the M4 layout (a regression guard for old revisions' re-exports, apart from the renderer version line).

Tests:

- **Property tests** (Hypothesis) over generated models: random templates, symbols with edge anchors, nets and connections; check invariants 1 to 4, determinism (two runs equal; permuting input order of ids that do not affect ordering gives an equal layout), and termination within budgets.
- **Geometric checks**: overlap and crossing counts computed from the layout data, with crossing counts recorded per fixture as a ratchet (may go down, failing if up without a note).
- **Held-out, data-only configurations**: as M4's `drawing_heldout_feeder`, at least one new configuration added after the router, in a commit that adds only JSON and fingerprint entries, plus the existing test that layout and renderer code names no configuration, template, designation or device word. Customer PDFs are used as data inspirations only, never as code paths.
- **Golden fingerprints**: `layout_sha256` and rendered-PDF fingerprints per fingerprint case; content change without a renderer version bump fails, as now.
- **Visual review samples**: PNG pages of each fixture attached to the PR for a human look; they are not a test oracle.

Review gates: the export is gated exactly as M4 (`SCHEDULE_FORMATS` path, v2 only, `purpose: execution` refused). A routed drawing from unreviewed templates is still produced in review mode with the template status shown, as now. No routed output is accepted as an approval artefact for anything beyond review.

## 6. Risks of misleading the reviewer

| Risk | Mitigation |
| --- | --- |
| A line is read as the physical wire path, length or routing in the enclosure | Cover and legend state that lines show which terminals a connection joins, not where a wire runs. Line style identical for all vias except a label; no duct or bundle geometry. Proposed note on every sheet (M5-1) |
| A tidy drawing implies a complete or checked design | Open-item counts on the cover and per sheet; unrouted and unconnected stay red as in M4; the review-only label unchanged on every sheet |
| A bus or bridge implies a busbar/jumper exists | A bus is drawn only on explicit `display: bus` and only through held connections; its label is the net's signal; `via` printed on every segment |
| A crossing read as a joint, or a joint missed | Junction dots only at model nodes; crossings never dotted; property test 2 |
| Cable grouping implies cores in one cable are a real cable spec | Only `cable_id`, `index`, `label` printed as held; no type, size or count invented |
| Symbol shape implies a device type or standard symbol | Symbols are data; template ref printed under each block; no standard named; review status of the template printed |
| A small edit reorders the whole drawing and hides the diff | Stable placement in schedule order; placement hints; per-revision `layout_sha256` |
| Cross-sheet lines hidden | Cross-references kept on every end (M5-2) |

## 7. Engineering decisions for the owner and a qualified engineer

Building on M4 D-1..D-9 and M3 O-1..O-12; none is decided by this proposal.

| # | Decision | Proposal default |
| --- | --- | --- |
| M5-1 | Whether routed lines are acceptable on a review drawing at all, and with what wording about physical routing (refines D-2) | Lines on, with a fixed note; owner and QE to confirm |
| M5-2 | Whether cross-references stay printed on routed lines (refines D-3) | Yes, always |
| M5-3 | Line conventions: junction marking, crossing style, bus representation, line weights per `via` (depends on D-1/O-2) | Neutral: dots only at model nodes, one weight; no standard claimed |
| M5-4 | Whether design-side layout hints need review, and by whom | Not reviewed; recorded in the revision |
| M5-5 | Who authors pin sides and symbol parts, and whether a symbol may be reviewed apart from its template (extends D-4) | Part of the template digest, as M4 |
| M5-6 | How strip points are drawn with anchors (closes D-7) | Pitch-based repeat, no invented markings |
| M5-7 | Whether jumpers and busbars get a distinct drawing form (depends on O-7) | No distinct form until O-7 is decided; label only |
| M5-8 | Whether cable grouping is drawn, and what cable data is shown | Grouping by `cable_id` only, values as held |
| M5-9 | Sheet size, density and whether one board may span sheets with routed lines across them (extends D-9) | A3, one board per sheet run, no lines across sheets |
| M5-10 | Placement strategy priority: stability across revisions vs fewest crossings | Stability |
| M5-11 | Budgets: maximum size routed per sheet before falling back to references | Set from fixtures, reviewed with owner |
| M5-12 | Whether unrouted connections should block review sign-off of a revision, or be informational | Informational; review status unchanged |

D-5, D-6, D-8 and O-3..O-6, O-8, O-12 are unchanged: printed only as held, life-safety stays out of execution scope, execution stays blocked.

## 8. Milestone slicing

| Slice | Content | Acceptance |
| --- | --- | --- |
| M5a: schema | Optional pin `side`/`order`, symbol `parts`, strip anchors, design `layout` hints, net `display`; strict codec; size limits | Every existing fixture's canonical JSON, `registry_lock`, subject digests and snapshot hashes unchanged; malformed additions refused with named errors; round-trip property tests; no renderer change |
| M5b: ports and same-column routing | Routing module, ports from data, channel routing within a sheet for both ends in adjacent columns; unrouted open item; `layout_sha256` | Invariants 1 to 5 hold on all fixtures; no-hint fixtures equal M4 layout; deterministic across runs; renderer version bumped with fingerprints |
| M5c: full sheet routing and crossing reduction | Placement reorder, grid fallback, nudging, crossing counts, budgets | Property tests for overlap/crossing; budgets enforced by counts; stress configuration finishes within budget or reports unrouted; crossing ratchet recorded |
| M5d: buses, jumpers, cable groups | `display: bus`, bridge drawing for adjacent jumpers, cable bundle labels | Bus taps equal joined members; nothing drawn for an unjoined member; no value printed not in the snapshot |
| M5e: multi-sheet and held-out | Sheet partition by connected components, cut connections as cross-references; one new held-out configuration added as data only | Held-out commit adds only JSON and fingerprint entries; data-only name test passes; every cut connection listed with both cross-references |
| M5f: reviewer wording | Cover counts, legend notes per M5-1..M5-3 once decided | Owner/QE sign-off of the wording recorded; review-only label and execution refusal tests unchanged |

Each slice ships with the review-only gating unchanged and `purpose: execution` still refused.
