# M9b1 — Guided pattern bindings and a server-composed wiring preview

Status: draft, 2026-11-16. Builds on `main` after #219 (`2886843`). Not for
deployment. Editing connections is M9b2.

## 1. Problem

A binding applies a connection pattern to a board: it says which components
fill the pattern's slots, which strips play its strip roles, the marking of
each point, and the cable a cable-core connection uses. Until now a binding
could only enter a configuration as JSON; the editor showed existing ones and
their point markings only.

## 2. What the user can do

In the configuration tab, **Bindings** section, without JSON:

1. **Add a binding**: a pattern the configuration holds, a board, and a circuit
   of that board or none.
2. **Slots**: for each slot, a configuration component on the binding's board
   whose definition is the slot's template (any component on the board when the
   slot names none), or _not chosen_.
3. **Strip roles**: for each role, a strip on the binding's board.
4. **Markings**: the marking printed for each point, typed by the user.
5. **Cable**: offered only when the pattern has a cable-core connection: one of
   the board's cables, or none.
6. **Preview**: the nets, connections, wires and strip points this binding would
   produce, composed by the server from the configuration as edited. It is
   labelled _unsaved preview, review only, not electrical validation_.
7. **Edit or remove** a binding; save with the existing Save.

Nothing is inferred: no slot, strip, marking, cable or connection is filled in.
Wire data (`wires`) and fields this slice does not edit are kept as stored.

## 3. Storage and API (unchanged)

No new storage, schema, codec, renderer, migration or endpoint. A binding is the
composer's existing shape in `configuration.bindings`:

```
{id, pattern, board, circuit, slots: {slot: {component}}, strips: {role: strip},
 markings: {point: text}, cable, wires: {}}
```

The preview calls the existing read-only
`POST /design/projects/{id}/configuration/validate`, which composes the
configuration against the latest saved revision's request and saves nothing:
no revision, no approval, no change to the project. The browser computes no
wiring; it only picks the preview rows that belong to the binding by the ids
the composer derives from the binding (`derive("x", binding, role, index)` for
connections, `derive("w", …)` for wires, `derive("p", strip, binding, point)`
for points, `derive("n", binding, role)` for binding-scoped nets).

A preview answers the configuration it was asked for. A response for an older
configuration never replaces a newer one, and any edit marks the shown preview
outdated until it is refreshed. The preview uses the latest **saved**
load schedule; unsaved load-schedule edits are not in it (it says so).

## 4. Checks and references

- Problems a save would be refused for are said beside the field before saving
  (pattern or board not in the configuration, circuit or cable not on the
  board, a slot's component missing, on another board or on another template,
  a role's strip missing or on another board, a point without a marking). The
  server still decides; a refused save names the binding, marks it, says the
  reason beside the binding and in the overview, and saves nothing.
- A component, strip or pattern a binding names cannot be removed; the binding
  is named. A selection that no longer resolves stays shown as such; it is
  never cleared or replaced.
- Feeder-link ends on a binding's points follow M8: removing the binding takes
  its points out of the picker; a chosen end stays visible and invalid.

## 5. Unchanged

Renderer versions, snapshot schemas, codecs, composer rules, approval and
review anchoring, tenant isolation, execution export blocked, restricted
formats refused, JSON import.
