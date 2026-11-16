# M9c — Guided wire details

Status: draft, 2026-11-16. Builds on `main` after #221 (`626f688`). Not for
deployment. Removes the last JSON step of the guided pattern workflow: wire
details (M9a patterns, M9b1 bindings and preview, M9b2 connections).

## 1. What the user can do

In each binding, **Wire details** lists every `wire` connection of the
binding's pattern, named by its board, binding, pattern, net (and signal) and
readable ends as the composer names them (`KA:A1 to XP:7`). The key the
composer uses (`role/index`) is never asked for. For each wire the user can:

1. **Enter or edit** the wire number, cross-section (mm²) and colour. A typed
   value is stored as entered by the user (`source: "user"`).
2. **See stored values exactly**, with their source: entered by you, from
   imported data, or an assumption with its statement. A value with a source
   other than `user` is read only.
3. **Replace** such a value with their own, after a confirmation that names the
   value and source being replaced. The field is then empty until a value is
   typed: nothing is copied over as if the user had entered it.
4. **Remove** one value or all details of a wire, after a confirmation that
   lists what is removed.

A field nobody gave stays unset; nothing is defaulted, suggested or
calculated. The section says that values entered here are not checked by an
engineer or by the design rules. Calculated design values (cable
cross-sections of the load schedule) are separate and unaffected.

## 2. Storage and API

No new storage, schema, codec, renderer, migration or endpoint. Details are the
composer's existing shape, `bindings[].wires["role/index"]` with fields
`number`, `cross_section_mm2` and `colour`, each either text (source `data`) or
`{value, source: user | data | assumption, statement?}`. Save, revision storage
and the server-composed preview (`configuration/validate`) are unchanged. The
wire schedule and connection drawing already print each wire's values with
their source; they are not changed.

**One server change, to the error path only.** The stored v2 model has always
held a cross-section as a positive plain decimal (at most six digits before
and after the point) and wire text as at most 200 characters; a value outside
those bounds used to pass the composer and fail later in the snapshot reader as
an unnamed `composition_invalid`. The composer now checks the same bounds and
refuses with `configuration_invalid` naming the `binding`, `wire` (the key) and
`field`; its existing wire refusals name the binding and wire too. Nothing that
was accepted before is refused now; stored snapshots, hashes, renderer versions
and historical revisions are unchanged (old revisions are never recomposed).

## 3. Checks said beside the field

| Problem                                            | Server refusal                  |
| -------------------------------------------------- | ------------------------------- |
| empty value                                        | `configuration_invalid` (field) |
| more than 200 characters                           | `configuration_invalid` (field) |
| cross-section not a positive plain decimal         | `configuration_invalid` (field) |
| stored source other than user, data, assumption    | `configuration_invalid` (field) |
| a stored name in an entry that is not a wire field | `configuration_invalid`         |
| details for no wire connection of the pattern      | `unknown_wire`                  |

Each problem is said beside its field (or wire) and listed at the top of the
section with a link that moves focus to the field; the binding's problem count
includes them. A refused save saves nothing; the draft keeps the edit through
a reload.

## 4. Reference integrity

Details are keyed by the connection's position in its net, so the M9b2 guards
apply: editing a connection that has details, removing a connection at or
before one that has details, and removing a net with details are refused,
naming the binding and key; the notice now says to remove the details first.
In addition:

- **Board change** of a binding is refused while it holds details: on another
  board they would describe a different physical wire.
- **Removing a binding** that holds details first lists every detail it holds
  and asks for confirmation.
- **Details held for no wire connection** (a key the pattern no longer has, or
  a connection that is not a wire) are kept, said, and removed only when the
  user confirms.
- **Unknown names** inside a wire's entry are kept untouched and said; editing
  another field never drops them.

Removal is always explicit and listed first. Saving creates a new revision;
earlier revisions and their exports are unchanged.

## 5. Unchanged

Renderer versions, snapshot schemas, codecs, export hashes, history, approval
binding (a new revision holds no approval), review anchoring, tenant isolation,
execution export blocked, restricted formats refused, JSON import.
