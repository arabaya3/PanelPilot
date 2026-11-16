# M9d — Reusing a binding on another board, and the bindings overview

Status: draft, 2026-11-16. Builds on `main` after #222 (`2122d17`). Not for
deployment. No server, schema, storage, codec or renderer change.

## 1. Use on another board

Each binding has **Use on another board**. The user chooses the board (there is
no default, and the binding's own board is not offered) and a name for the new
binding (a free one is suggested: the source id and board, numbered if taken;
any typed name is checked like a new binding's).

**Add the draft binding** adds, to the configuration being edited, a binding in
the composer's existing shape:

```
{id, pattern: <the source's pattern>, board: <chosen>, circuit: null,
 slots: {}, strips: {}, markings: {}, cable: null, wires: {}}
```

- Only the pattern **reference** is reused: the pattern is shared, not copied,
  and its connections (with any cable-core numbers they state) are not changed.
- Nothing board-specific is copied or chosen: no circuit, component, strip,
  point marking, cable or wire detail; no compatible part is inferred and no
  first option is picked.
- The source binding is not changed.
- Nothing is saved. The new binding is completed field by field, previewed and
  saved with the existing Save, like any binding. A refused save creates no
  revision, and the draft keeps both bindings through a reload.

Below the new binding, a summary (for this session) says what was reused (the
pattern, its connection count, the bindings it is shared with) and what must be
entered (slots, strip roles, point markings, a cable where a connection uses a
cable core, optional wire details). Where the pattern has cable-core
connections, their core numbers are said to be part of the shared pattern, not
of either binding. The pattern editor names every binding that uses a pattern,
with its board, so a pattern edit shows each board it reaches.

## 2. Bindings overview

At the top of the bindings section, bindings grouped by board, each with its
pattern and what is known against it, kept apart by where it comes from:

| Source            | What                                                                 |
| ----------------- | -------------------------------------------------------------------- |
| Local checks      | The editor's checks of the configuration as edited (bindings, wires) |
| Last save refusal | The server's refusal of the last save, where it names this binding   |
| Last validation   | Findings of the last server validation naming this binding's parts   |

Each local problem links to its field; the binding's name links to the binding.
Validation findings are attributed to a binding when their location names one of
its parts by the ids the composer derives (connections, wires, points, its own
nets, the components it fills its slots with); a board-scope net shared with
other bindings is not attributed. Once the configuration changes, the last
validation's findings are shown as outdated, never as current. Nothing in the
overview says a binding is complete, correct or safe: an empty list only means
nothing was found there. The overview reuses the existing checks and findings;
it adds no rules.

## 3. Unchanged

Storage, schemas, codecs, composer, renderers, export hashes, history, approval
binding, review anchoring, tenant isolation, execution export blocked,
restricted formats refused, JSON import.
