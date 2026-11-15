# M8 — Guided component definitions as feeder-link endpoints

Status: draft, 2026-11-15. Builds on `main` after #216 (`9b95ca3`). Not for
deployment.

## 1. Problem

A component's terminals come from its template. The guided editor (M7b) could
place components only on templates already present, and the shipped
`global/legacy-*` templates define no terminals. So a component with terminals,
and a feeder-link end on one, needed pasted or imported JSON.

## 2. What the user can do

In the configuration tab, without JSON:

1. **Add a component definition**: an id (`[a-z0-9][a-z0-9_.-]*`, stored as
   `tenant/<id>/v1`) and a function chosen from `unspecified` or a function
   already present in the project's definitions.
2. **Add terminals**: each marking as typed; kind `unspecified` unless a kind
   already present in the project's definitions is chosen; "must be connected"
   `not stated` unless the user says yes or no; an optional source document.
3. **Place a component** on that definition (the existing "Add a component").
4. **Pick one of its terminals as a feeder-link end** on its own board, before
   saving.
5. **Save once**: the existing Save sends the request (with the link) and the
   configuration (with the definition and the component).

## 3. Storage and model (unchanged)

No new storage, schema, codec, renderer or migration. A definition is the
composer's existing template shape in `configuration.templates` (M2 §3, M7b §2):

```
{ref: "tenant/<id>/v1", function, terminals: [{id, marking, kind, required, group: null}],
 contact_groups: [], parameters: [], restricted_scope: [],
 provenance: {source: <text or null>, author: "user-entered", created: <date>},
 review: {status: "unreviewed", reviewer: null, reviewed_at: null, standards: [], scope_note: null}}
```

- Nothing is inferred: no rating, parameter, symbol, contact group or
  connection; `unspecified` and `null` stay so. `author` records no account data.
- Every definition entered here is `unreviewed`; the existing
  `template_unreviewed` finding (warning, engineering confirmation) flags it.
  Requiredness is documented only with a source (`entities.explicitly_required`),
  so without one an unconnected terminal is `terminal_requiredness_unresolved`.
- The revision stores the configuration verbatim in its requirements record;
  the snapshot embeds the template and locks its digest. Editing a definition
  saves a new revision; earlier revisions, their configuration, snapshot and
  hashes are never rewritten. The same ref may hold different terminals in
  different revisions of one project; each snapshot locks its own copy.

## 4. Identity

- Terminal ids are `t-<sequence>-<random>`: the zero-padded sequence keeps the
  composer's id order equal to the order of entry; the random part means an id
  is never reissued, so a link that named a removed terminal never lands on a
  new one.
- The picker offers terminals of the configuration being edited by the ids the
  server derives (`ids.derive("k", component)`, `ids.derive("p", strip, "direct",
point)`; computed in the browser with SHA-256, checked against the API's
  values in tests). Saved-model terminals the configuration no longer holds are
  not offered; terminals only the saved model holds (pattern points) still are.

## 5. References

- A definition used by a component or strip cannot be removed; the users are
  named with a way to each.
- Renaming a definition names the entries it updates and points them at the new
  ref in the same step (the version is kept).
- A terminal, component or strip point used by a feeder-link end in the form
  cannot be removed; the link end is named.
- A link end whose terminal is no longer in the configuration is kept and said
  ("no longer in the configuration"), never silently cleared.
- The server still refuses anything else (unknown template, unknown link
  terminal, invalid template) and saves nothing; a refusal naming only a
  template ref marks that definition with an Edit action.
- Errors are shown beside the field (id, marking).

## 6. Exports

The connection drawing and wire schedule show the endpoint by designation and
marking (`terminal_link_end: MB-1/Q9:L1 … LINK END … feeder link to SB-1`), with
any unmet board-local requirement as its own note (M7b round 3). The JSON export
carries the link's terminal ref within its stated scope; the configuration
itself is not in it.

## 7. Unchanged

Renderer versions, snapshot schemas, codecs, approval rules and binding, tenant
isolation, execution export blocked, restricted formats refused. Configurations
and links still need `DESIGN_SNAPSHOT_SCHEMA` `pp-design/2` (configuration) and
`pp-design/3` (links); the default `pp-design/1` is unchanged.
