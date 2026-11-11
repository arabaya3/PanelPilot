# M7b — Preserved configuration and guided component/terminal entry (contract)

Status: integration-owner contract, 2026-11-10. Builds on #215 (`a4602a7`).
Draft; not for merge or deployment.

## 1. Problem

A configuration (templates, patterns, strips, components, bindings) is sent
only with one save and survives only composed inside that revision's
snapshot. A later plain save, an answer or a re-save drops it or is refused
(`configuration_not_carried`). Entry is pasted JSON.

## 2. Storage

- The configuration a revision was composed from is stored **verbatim in that
  revision's requirements record** under `configuration` (absent when none).
  The requirements hash, and so the approval binding, cover it with no new
  column or hash. A record without the key reads exactly as before (v1/v2/v3
  records stored byte for byte unchanged when no configuration is sent).
- Historical revisions are never rewritten.

## 3. Carry-forward (every save path)

| Path                                                   | Configuration used                                            |
| ------------------------------------------------------ | ------------------------------------------------------------- |
| save with `configuration`                              | the one sent                                                  |
| save without it (plain save, answer, feeder-link edit) | the latest revision's stored configuration, if any            |
| re-save of revision n                                  | revision n's own stored configuration                         |
| explicit removal                                       | `configuration: null` sent with `configuration_removed: true` |

- The carried configuration is re-validated and re-composed against the new
  request. A reference that no longer resolves (a bound device/circuit/board/
  cable gone, a feeder-link terminal gone), or any other composer refusal of
  the carried configuration, refuses the save with 409
  `configuration_conflict`, `params`: `{ref, kind, binding|strip|component|link,
reason}`. Nothing is saved; nothing is dropped or regenerated.
- A revision whose snapshot holds a configuration but whose record has none
  (saved before M7b) cannot be carried losslessly: saves without a configuration
  are refused with 409 `configuration_not_recoverable` (`revision`); the user
  re-enters it. Re-save keeps `configuration_not_carried`.
- Revision switching opens the selected revision's own stored configuration;
  saving from it carries that one.

## 4. Open revision

`SavedProject.configuration` = the stored configuration (or `null`), and
`SavedProject.configuration_status` ∈ `none | stored | not_recoverable`.

## 5. Guided editor (web)

- Edits the same configuration document; no JSON needed (JSON import stays as an
  advanced option).
- Add a component or terminal strip from template definitions already present
  in the project/registry; point rows (designation/marking only as typed by the
  user; no invented marking, rating, symbol or value). Review status and
  provenance of templates are shown as stored; unreviewed/unknown stays marked.
- Feeder-link endpoints chosen from terminals on each end's own board, shown by
  designation and board name, never raw id.
- **One Save action** sends the form request plus the current configuration.
  A failed save keeps every input. `configuration_conflict` is shown with the
  named reference and a recovery action (edit or remove the binding).
- Graphite/gold styling, keyboard operable, en/ar/he, long choices wrap on mobile.

## 6. Unchanged

Renderer versions, codecs, snapshot schemas, approval rules, execution export
blocked, restricted formats refused, no electrical values added.

## 7. As built

- The web Save always sends the configuration shown in the editor, so a save
  from an older revision stores that revision's own configuration.
- A design refusal of the request itself while a configuration is carried is
  the existing 422 `configuration_refused` (refusal `design_refused`), not a
  conflict; nothing is saved, so the configuration is not dropped.
- `configuration_not_recoverable` can be left only by re-entering the
  configuration or by ticking an explicit "save without that configuration".
- The first save of a project (`save_project`) still takes no configuration.
