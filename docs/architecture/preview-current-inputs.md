# Preview the current load schedule and configuration together

Status: draft, 2026-11-17. Builds on `main` at `8508fcd` (#223, #224). Not for
deployment.

## 1. Problem

The configuration preview (`POST /design/projects/{id}/configuration/validate`)
composed the configuration against the **latest saved** revision's load
schedule. A circuit or cable added in the editor but not yet saved could not be
previewed (M9d hit this: `unknown_reference` for a new sub-board cable), and the
circuit and cable choices came from the last Design run, whatever had changed
since.

## 2. What the user can do

- The preview and the panel's Validate send the load schedule **as the editor
  holds it now** with the guided input (`requirements_input`), exactly as Save
  sends them, with the configuration. The server runs Save's own checks on them
  (section 3). Nothing is written: no revision, no approval change, no draft on
  the server, no AI proposal marked used.
- It is labelled _Unsaved preview of the current load schedule and
  configuration_, with the note that a preview is not a save and does not
  guarantee one. The preview's staleness key covers the configuration, the load
  schedule, the guided input and the confirmations (below): any change makes a shown preview
  outdated, and an answer for older inputs is shown only as outdated, never as
  current.
- Circuit and cable choices come from the server's design of the current
  inputs: the preview's answer carries each board's circuits and cables
  (`boards`). Choices from a Design run are used only while that run matches the
  current schedule; otherwise the bindings say the choices are not checked and
  offer **Refresh circuit and cable choices** (a preview of the design alone).
  The browser computes no circuits or cables.

## 3. Preview and Save run the same checks

Shared code, not a second engine: `_check_save_input` (company profile, typed
feeder rows, guided input: `control_load_unknown`,
`control_need_requires_controlled`, `control_given_twice`), `_check_room`
(`project_revisions_full`), `_validate` (design, feeder links, configuration,
reference identity, composition, cable-core gate, link derivation, hash),
`design_ai.apply(lock=False)` (the AI proposal and its placements, read only),
and `requirements.build` (the record a save stores, built and discarded).

What only a save can settle, because it depends on state at the moment of
saving, is listed in the review's `checked_at_save_only` and in the editor's
note:

| Item                | Why a preview cannot settle it                                     |
| ------------------- | ------------------------------------------------------------------ |
| `revision_conflict` | Another revision can be saved between the preview and the save.    |
| `revision_limit`    | Checked by the preview, but the count can change before the save.  |
| `access`            | Membership and permissions are checked again on the save.          |
| `ai_proposal_use`   | A proposal can be used by another save first (`ai_proposal_used`). |

A clean preview is therefore never presented as a guarantee that saving will
succeed. Tests: `test_preview_current_inputs_db.py` (guided input refused alike,
full project refused alike, feeder links, invalid requests, read-only).

## 4. Reference identity

Circuit ids are positional (`c{n}` for the n-th circuit of a board, the
server's feeder circuits after the entered loads), and so are their cables
(`c{n}-cable`) and devices (`c{n}-breaker`, …). Inserting, removing or
reordering loads, or renaming, reordering or deleting boards, changes what an id
names. The model has no stable load identity; adding one would change stored
ids, hashes and renderers. Instead, a reference is accepted only where its
identity is **proven**, on preview and save alike.

**Checked.** Every binding reference (its circuit, its cable, each slot filled
by a design device) whose id is the same as in the configuration saved with the
latest revision, **whether or not the binding's board changed**. A reference
whose id the user changed, a binding new since the last save, or a project with
no saved configuration was chosen against the current choices; the composer
checks it as before (`unknown_reference` for an id that no longer exists).

**Proven** only when the load the id names now (on the binding's board now) has
the same fingerprint as the load it named in the latest revision (on the board
the binding named then). The fingerprint is:

- for an entered load, **every field entered for it**: description, kind,
  power, phases, power factor, control, starter and length;
- for a server feeder, the name of the board it feeds and that board's position;
- and the binding context the design gives it: its devices' roles and whether
  it has a cable;
- for a board's own device named by a slot: an incomer by the board as entered
  (all but its name and loads) and its position; any other board device (a
  residual current group's `g{n}-rcd` or its supply breaker `g{n}-breaker`,
  positional too) by its kind, the fingerprints of every circuit it feeds
  through the devices below it, and those devices' kinds. A board device that
  feeds no circuit is never proven.

Description and kind alone are **not** treated as identity. Anything else is
unproven and refused: another load at that position, a reordered, swapped,
renamed or deleted board, a rename chain, a changed binding context, a
renumbered residual current group, or a latest revision whose design or request
cannot be read (on either side: unreadable data never proves anything). A positional id that still
exists is never accepted on that ground alone.

**Identical loads.** Two loads equal in every entered field and binding context
that swap places are indistinguishable in the model: the reference is accepted,
and the composed model is the same either way. A swap that changes anything the
model records about the load (power, phases, control, kind, devices, …) is
refused.

**Refusal.** `configuration_refused`, refusal `reference_moved`, naming the
first unproven reference (`binding`, `field`, `key`, `ref`, `before`, `after`,
`before_board`, `after_board`, `before_circuit`, `after_circuit`, `token`), each
of the first 20 in full (`r{i}_binding`, `r{i}_field`, … `r{i}_token`), their
number (`moved_count`), and a compact list (`moved`, within the 200-character
parameter bound). The editor says what keeping means for each one named in full
and how many more there are; those are named once the first are settled.

**Keep.** The editor says each one beside its binding, links to its field, and
says what keeping means: _“Keeping c1 means this binding connects to “Heater”
(c1 on MB-1) instead of “Lighting” (c1 on MB-1). Its wire details stay exactly
as entered; they are not checked for the new load.”_ Keeping sends a
confirmation `{binding, field, key, id, token}`. The token is a hash of the
reference and its exact before and after (board and fingerprint): any later
edit of either load, the board or the reference gives another token, so the
confirmation no longer applies and the reference is refused again.
Confirmations go with the preview and the save they apply to, are kept with the
editor's draft through a reload (a draft confirmation without a token is
dropped), and are **never stored**. Nothing is changed for the user: the
reference, its wire details and feeder ends stay as they were.

**Scope.** A save with the configuration sent, a save that carries the stored
configuration, and the preview of the current inputs. Not answer-generated
revisions (the answer flow cannot confirm a reference) or a re-save of an
earlier revision (its request and configuration were composed together).
Historical revisions are read, never changed.

## 5. Compatibility

- **API.** Additive: `request`, `requirements_input` and `references_confirmed`
  on the validation, `references_confirmed` on a revision,
  `from_current_request`, `boards` and `checked_at_save_only` on the review. A
  validation without `request` behaves as before. A confirmation requires the
  refusal's `token` (new in this change; nothing released used it).
- **Behaviour change.** A save whose edit leaves an unchanged binding reference
  unproven (section 4) is now refused (`reference_moved`) instead of silently
  re-attaching the binding. This applies to API callers too; they keep the
  reference with `references_confirmed` (with its token) or change it. Board
  renames, reorders and deletions now ask for confirmation of the affected
  references.
- **Unchanged.** No snapshot schema, codec, renderer or migration change; stored
  revisions, hashes, exports and approval bindings are untouched (old revisions
  are not recomposed); `DESIGN_CABLE_CORE_CROSS_BOARD` stays off by default.

## 6. Limitations

- A preview is not a save: what only a save can settle is listed in section 3.
- Identity is proven from what the model records. Two loads equal in every
  entered field and binding context are indistinguishable; swapping them is
  accepted (the composed model is the same).
- Any change to a bound load's entered fields (even its description or length)
  makes its references unproven: they are refused until kept or changed.
- Board renames, reorders and deletions are never followed automatically; the
  affected references are refused until kept or changed.
- The refusal names every unproven reference only within the server's
  parameter bound; the first is always named in full, and the editor re-checks
  after each confirmation.
- A confirmation lives in the editor's draft, not in the project: it is lost if
  the draft is discarded before saving.
- Kept wire details are not checked for the load now there; nothing here
  states electrical suitability.
