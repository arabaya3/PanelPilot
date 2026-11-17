# Cable-core identity: (board, cable, index)

Status: draft, 2026-11-17. Builds on draft #223 (M9d, `0d621ed`), which builds
on `main` at `2122d17`. Not for deployment.

## 1. Problem

v1 numbers each board's cables from its own circuits (`c1-cable`, `c2-cable`,
…), so `c1-cable` on MB-1 and `c1-cable` on SB-1 are two different cables. The
pp-design/2 model already treats a core's `cable_id` as a cable **of the
connection's board**: the reader resolves it in `boards[net.board_id].cables`
and refuses "names no cable of the net's board"; the composer resolves a
binding's cable among its own board's cables. But the rule "a core makes one
connection" was keyed by `(cable_id, index)` alone, in the composer, the
stored-model reader and the editor's check. Reusing a cable-core pattern on two
boards (M9d) with core 1 of each board's `c1-cable` was therefore refused as
`core_reused`, although these are two different cores.

## 2. Identity contract

A cable core is identified by **(board, cable id, index)**, where the board is
that of the connection's net. Nothing new is stored: the board is already part
of every connection (through its net), and `cable_id` already resolves within
it. The rule "one connection per core" applies to that identity in all three
places:

| Layer                          | Before                | Now                               |
| ------------------------------ | --------------------- | --------------------------------- |
| Composer (`compose.connect`)   | `(cable_id, index)`   | `(board, cable_id, index)`        |
| Reader (`codec_v2`)            | `(cable_id, index)`   | `(net.board_id, cable_id, index)` |
| Editor check (`binding-model`) | bindings on any board | bindings on the same board        |

Unchanged and still refused: a core used twice on the same board's cable
(`core_reused`; the reader: "a core of one board's cable makes two
connections"); a cable the binding's board does not have (`unknown_reference`,
naming the binding); a core past the cable's cores (`core_out_of_range`); a
stored core naming no cable of its net's board. A cable reference is never
resolved on another board, and no board is guessed: a binding has exactly one
board, and a connection exactly one net.

## 3. Schema, storage, renderer and approval impact

- **No new schema version.** The meaning of every stored field is unchanged:
  `cable_id` has always named a cable of the net's board (M2 §"references
  resolve"). The M2 table's uniqueness key `(cable_id, index)` was written with
  `cable_id` as a v1 cable, which is board-scoped; it is now stated as
  `(board, cable_id, index)`.
- **Existing revisions** satisfy the old, stricter rule, so they read exactly as
  before: same model, same canonical dump, same `snapshot_sha256`, same exports
  and approval binding. No stored revision is rewritten or recomposed.
- **New revisions** that use the same cable id and core on two boards are
  readable only by this reader or later. Reader and writer are deployed
  together; an older reader refuses such a snapshot as unreadable (it never
  misreads it). This is the one forward-compatibility effect.
- **Renderers unchanged** (no renderer version change): the connection drawing
  prints each core as held (`cable c1-cable core 1, core label …`) beside its
  own board's terminals, as before. The wire schedule does not print cores.
  The API's review row keeps its `cable#index` form; the board is its net's.

## 4. Unchanged

Wire details, electrical values and connections (nothing is invented), tenant
isolation, review anchoring, execution export blocked, restricted formats
refused, previews composed with the saved load schedule.
