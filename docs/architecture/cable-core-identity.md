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

## 4. Write gate, deployment and rollback

The reader above accepts a cable id and core on two boards, but a reader from
before this change refuses such a revision. Rather than accept that as a
deployment limitation, writing such a revision is **gated**:

- `DESIGN_CABLE_CORE_CROSS_BOARD` (server setting, **false by default**), read on
  each call like `DESIGN_SNAPSHOT_SCHEMA`. It gates **writing** only; such
  revisions are always read.
- Enforced in `design_configuration.compose_snapshot`, the one place every
  revision (save, revise, resave, answer-generated revisions through
  `_add_revision`) and every server preview (`configuration/validate`) is
  composed. With it off, the composer refuses the second board's use of the
  same cable id and core: `configuration_refused`, refusal
  `core_shared_cable_id`, naming the `binding`, `cable`, `core` and
  `other_board`; nothing is saved, and the preview refuses exactly as the save
  would. The editor says so beside the binding (it is a server setting, not an
  input error); the draft keeps the edit through a reload. Nothing is renamed,
  moved to another cable or repaired.
- With it off, every revision written is readable by the older reader
  (`app/tests/design/model/pinned/codec_v2_2122d17.py`, the pre-change reader,
  checks this in the tests).

Why a gate and not a version marker: a marker (a new snapshot schema or a
capability field) would not make older readers able to read the new revisions
either: they would refuse them by marker instead of by rule. It would also
need a new envelope, reader, writer and renderer-version work. The gate keeps
the existing schema and makes the incompatible case impossible to write until
an operator decides every reader can read it.

**Limit of the gate.** It preserves rollback only while no such revision has
been written. Once it is on and such a revision exists, older readers refuse
that revision (its view, exports and approval page); they never misread it.
The gate does not make older readers compatible afterwards.

**Procedure.**

1. Deploy this version everywhere with the gate off (the default). Mixed old and
   new readers are fine: nothing they cannot read is written.
2. When no older reader remains in service and rollback past this version is
   no longer planned, set `DESIGN_CABLE_CORE_CROSS_BOARD=true`.
3. Before any rollback past this version (or running older readers against the
   database), run the read-only audit:
   `python -m app.design.cable_core_audit` (same `DATABASE_URL` as the API). It
   prints project ids, revision numbers and the cable/core found on two boards,
   and exits 1 if any exist, 0 if none. If none: rollback is safe. If some:
   rolling back makes exactly those revisions unreadable to the older code;
   there is no automatic downgrade and historical records are not rewritten.
4. Turning the gate off again stops new such revisions (a resave of one is
   refused) but leaves those already written as they are, readable by this
   version.

## 5. Unchanged

Wire details, electrical values and connections (nothing is invented), tenant
isolation, review anchoring, execution export blocked, restricted formats
refused, previews composed with the saved load schedule.
