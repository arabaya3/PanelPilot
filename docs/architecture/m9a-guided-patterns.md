# M9a — Guided connection patterns (definitions only)

Status: draft, 2026-11-16. Builds on `main` after #218 (`562c9f2`). Not for
deployment. Guided bindings, a generated-wiring preview and broader wiring UX
are M9b.

## 1. Problem

A connection pattern (M2 §3) could only enter a configuration as pasted or
imported JSON. M8 made component definitions guided; patterns stayed JSON.

## 2. What the user can do

In the configuration tab, without JSON, a **Connection patterns** section:

1. **Add a pattern**: an id (`[a-z0-9][a-z0-9_.-]*`, stored as `tenant/<id>/v1`),
   with an optional source document. It is `user-entered` and `unreviewed`.
2. **Slots**: an id, and a template chosen from the configuration's definitions
   or the opened revision's registry templates, or none (the bound component's
   template is then used). `device_kind` is kept as stored; never set here.
3. **Strip roles**: names typed by the user.
4. **Points**: an id, its strip role, offset and level (integers ≥ 1), and a
   kind (`unspecified` or a kind already present in the project's patterns).
5. **Nets**: a role, the signal name the user types, a scope (`binding`, the
   default, or `board`), and members: a slot's terminal (picked from the slot
   template's terminals where the configuration holds them, else typed) or a
   point's side. Each net lists its members in words.
6. **Edit and remove** each of these, and rename a pattern (bindings that name
   it are repointed in the same step, after being listed). Save with the
   existing Save.

Connections (`via`, cable cores) are kept exactly as stored and counted; they
are not edited here (M9b). A member a connection names cannot be removed;
removing another member re-indexes the net's connections, so each still joins
the same two members.

## 3. Storage and model (unchanged)

No new storage, schema, codec, renderer or migration. A pattern is the
composer's existing shape in `configuration.patterns`:

```
{ref, slots: [{id, template, device_kind}], strips: [role], points: [{id, strip, offset, level, kind}],
 nets: [{role, signal, scope, members: [{slot, terminal} | {point, side}], connections: [...]}],
 provenance: {source, author: "user-entered", created}, review: {status: "unreviewed", ...}}
```

Nothing is inferred: no connection, `via`, core, rating, symbol or rule is
added; `unspecified` stays so. The revision stores the configuration verbatim
in its requirements record (covered by the requirements hash and approval).
Only patterns a binding uses are embedded in the snapshot, as before.

## 4. Server check (the one behaviour change)

Before M9a the composer checked a pattern's slot templates and terminal
membership only when a binding applied it, so an unbound pattern with a slot
on an unknown template, a terminal its template lacks, or one terminal in two
nets was saved as typed. The composer now checks **every** configured pattern
(as it already composes every configuration component, bound or not), with
the codes it already uses at binding time:

- `unknown_template` / `restricted_scope`: a slot names a template the
  configuration and registry do not hold, or a restricted one.
- `terminal_not_on_template`: a member names a terminal its slot's template
  does not have (slots without a template are checked when bound, as before).
- `pattern_invalid`: a member is listed twice in one net.
- `contradictory_nets`: one terminal or point side is a member of two nets of
  the pattern that would not be the same net (two `board`-scope nets with the
  same signal are one net, so they may share members).

Refusals name `pattern` (its ref) so the editor marks that pattern. Nothing is
saved. Compatibility: a stored configuration carrying such an unbound
inconsistent pattern would now be refused when carried into a new save
(`configuration_conflict`); opening, exporting and approving existing
revisions is unchanged, and their snapshots and hashes are untouched.

## 5. References

- A pattern a binding names cannot be removed; the bindings are listed.
- A slot, strip role or point a net member or binding names cannot be removed;
  the users are listed. A definition a slot names cannot be removed (M8).
- Renaming a pattern lists and repoints the bindings that name it.
- Errors are shown beside the field (ids, offset, level, signal, members).

## 6. Review and approval

Review status is anchored to the server's decision for the exact definition
digest (M3 `anchoring.definition_digest`, the canonical pattern with its
review unset). Any edit changes the digest, so an earlier decision does not
carry to the changed pattern. Editing a pattern changes the requirements
record, so it is a new revision; an approval stays bound to the revision it
approved. No status other than `unreviewed` is ever written here.

## 7. Unchanged

Renderer versions, snapshot schemas, codecs, approval rules, tenant
isolation, execution export blocked, restricted formats refused, JSON import,
bindings.
