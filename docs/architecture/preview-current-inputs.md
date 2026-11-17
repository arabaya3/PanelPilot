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
  holds it now** (the same request shape Save sends) with the configuration. The
  server checks, designs and composes both through Save's own validation (the
  same profile resolution, feeder-link checks, `build_design`,
  `check_configuration`, `compose_snapshot` and link derivation, so the same
  refusal codes, the cable-core write gate, and `configuration_requires_v2` on a
  deployment that writes v1). Nothing is written: no revision, no
  approval change, no draft on the server.
- It is labelled _Unsaved preview of the current load schedule and
  configuration_. The preview's staleness key covers the configuration, the
  load schedule and the confirmations (below): any change makes a shown preview
  outdated, and an answer for older inputs is shown only as outdated, never as
  current.
- Circuit and cable choices come from the server's design of the current
  inputs: the preview's answer carries each board's circuits and cables
  (`boards`). Choices from a Design run are used only while that run matches the
  current schedule; otherwise the bindings say the choices are not checked and
  offer **Refresh circuit and cable choices** (a preview of the design alone).
  The browser computes no circuits or cables.

## 3. Reference identity

Circuit ids are positional (`c{n}` for the n-th circuit of a board, the
server's feeder circuits included), and so are their cables (`c{n}-cable`) and
devices (`c{n}-breaker`, …). Inserting, removing or reordering loads therefore
changes what an id names, and a binding that still names `c2` would silently
be re-attached to another load. The model has no stable load identity; adding
one would change stored ids, hashes and renderers. Instead such a change is
detected and refused, on preview and save alike:

- A load is told apart by what the user entered for it: its description and its
  kind. A server feeder circuit is told apart by the board it feeds, followed
  through a rename, so renaming the fed board does not make its feeder another
  load, while reordering fed boards does.
- A board renamed since the last save (its saved name gone, a new name at its
  position) is the same board, so a
  binding that follows the rename is still compared against what its ids named
  before.
- For each binding reference (its circuit, its cable, and each slot filled by a
  design device) whose value is **unchanged** from the configuration saved with
  the latest revision, the server compares the load that id names in that
  revision's stored design with the load it names in the current design. If
  they differ, the configuration is refused: `configuration_refused`, refusal
  `reference_moved`, naming the `binding`, `field`, `key`, `ref`, `before` and
  `after` loads, and all moved references in `moved`
  (`binding|field|key|id;…`).
- The editor says each one beside its binding, links to the field, and offers
  **Keep {id} for “{after}”**. Nothing is changed for the user: the reference,
  the binding's wire details and feeder ends stay as they were. Choosing another
  circuit, cable or device is the other way out.
- Keeping is an explicit confirmation (`references_confirmed`:
  `{binding, field, key, id}`), sent with the preview and the save it applies to,
  kept with the editor's draft through a reload, and **never stored**.
- A reference the user changed, a binding new since the last save, an id that is
  new, and an appended load are not affected. An id that no longer exists is
  refused by the composer as before (`unknown_reference`).
- The check applies to a save with the configuration sent, a save that carries
  the stored configuration, and the preview of the current inputs. It does not
  apply to answer-generated revisions (the answer flow has no way to confirm a
  reference, so they compose as before) or to a re-save of an earlier revision
  (its request and configuration were composed together).

## 4. Compatibility

- **API.** Additive: `request` and `references_confirmed` on the validation,
  `references_confirmed` on a revision, `from_current_request` and `boards` on
  the review. A validation without `request` behaves as before.
- **Behaviour change.** A save whose load-schedule edit moves an unchanged
  binding reference is now refused (`reference_moved`) instead of silently
  re-attaching the binding. This applies to API callers too; they keep the
  reference with `references_confirmed` or change it.
- **Unchanged.** No snapshot schema, codec, renderer or migration change; stored
  revisions, hashes, exports and approval bindings are untouched (old revisions
  are not recomposed); `DESIGN_CABLE_CORE_CROSS_BOARD` stays off by default.

## 5. Limitations

- The preview does not run the requirements-answer checks of a save (it sends
  no requirements input), so a save can still be refused there after a clean
  preview.
- A board is followed through a rename only when its saved name is gone and the
  name at its position is new. Swapped names, rename chains (A→B while B→C) and a
  deleted board are not followed: a binding on such a board counts as changed
  by the user and is not checked, so a move on it is not detected.
- Two loads entered identically (same description, kind and fed board) that
  swap places cannot be told apart; such a move is not detected.
- Renaming a load's description counts as another load: its references are
  refused until kept or changed.
- The refusal names every moved reference only within the server's parameter
  bound (200 characters); the first is always named in full, and the editor
  re-checks after each confirmation.
- A confirmation lives in the editor's draft, not in the project: it is lost if
  the draft is discarded before saving.
