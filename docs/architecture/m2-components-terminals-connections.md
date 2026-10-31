# M2: reusable components, terminals, connectivity and wires

Status: design proposal only; nothing here is implemented. Revised 2026-10-31 after the owner's decisions. It builds on Phase 1 and M1 as in #208. This document makes no claim of compliance, conformity with a standard, or engineering readiness.

## 0. Scope and naming

**M2: reusable components, terminals, connectivity and wires** (owner decision, 2026-10-31). The roadmap in `dynamic-design-engine.md` Section 12 is renumbered to match:

| Milestone | Scope                                                                                                                                                                                                                                                                                                               |
| --------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| M1        | Read-side versioning (done, #208)                                                                                                                                                                                                                                                                                   |
| **M2**    | This document: component templates as data, component instances, terminal strips and points, nets, connections, wires and cable cores in `pp-design/2`; the `registry_lock` for templates                                                                                                                           |
| M3        | Rule registry: provenance and versions for existing rules, `rule_ref` on findings, CalcRecords; then the rules, approved by qualified engineers, that fill M2's open values (wire numbers, cross-sections, colours, terminal ratings) and graph rules such as PE continuity; rule versions join the `registry_lock` |
| M4–M7     | Catalogue, logic, layout, execution-readiness gate (formerly M5–M8)                                                                                                                                                                                                                                                 |

**Where the rule registry fits.** M2 comes first because it adds no engineering rule. It gives every rule-driven value a place in the model, and records whether the value is user-entered, a recorded assumption, or empty. M3 then registers rules with their sources. No M2 value may be filled by an unregistered rule, and no rule is registered without the approval of a qualified engineer (architecture D-5).

**Customer PDFs are test cases, not fixed families.** Configurations come from templates and connection patterns as data (Section 3). No code path or template is named after, or built for, a reference document, and the golden corpus records which capabilities each anonymised case exercises, not which customer it came from.

M2 adds no electrical calculation. Wherever a value needs an engineering rule (cross-section, colour, wire-number format, terminal rating), the model has a field for it, records where the value came from, and leaves it empty or assumed until an approved rule exists.

## 1. What M2 builds on (as built in M1)

- `versions.py`: `ORDER = (V1,)`, `ENVELOPED` empty, `MIGRATIONS` empty, `identify`/`payload`/`migrate`. A bare snapshot is v1. A later schema is stored as `{"schema", "model"}` with exactly those two keys. An envelope that names `pp-design/1` is refused.
- `read.py`: `read_snapshot` is the only reader. It checks strictly, so an unknown field is `SnapshotUnreadableError`. The caller checks the hash first.
- `domain.py` / `convert_v1.py`: frozen v1 mirror, with `unmapped_v1_fields() == {}` enforced.
- `design_projects.py`: the server builds the snapshot (`designed.model_dump(mode="json")`) and hashes it with `snapshot.sha256_of`. An approval records the snapshot hash, profile hash, rules, catalogue and renderer versions and the findings fingerprint. It counts only while `RENDERER_VERSION` is unchanged.

## 2. The `pp-design/2` model

### 2.1 Envelope

```json
{ "schema": "pp-design/2",
  "model": { "design": {…v1 DesignProject content, unchanged…},
             "templates": [ …ComponentTemplate… ],
             "registry_lock": { "templates": [ {"ref": …, "digest": …} ] },
             "components": [ … ], "strips": [ … ], "nets": [ … ],
             "connections": [ … ], "wires": [ … ],
             "assumptions": [ … ] } }
```

- `design` holds the v1 `DesignProject` exactly as v1 validates it. Boards, devices, cables, circuits, notes, parts and title block keep their v1 meaning. v2 **adds** to v1 and replaces nothing. This keeps one projection to v1 for the existing renderers (Section 4).
- `templates` **embeds** a copy of every template definition the design uses. A stored v2 snapshot can then be read, checked and rendered without the live registry, and its hash covers what the terminals mean. `registry_lock` records each template's ref and the canonical SHA-256 of its embedded definition. A mismatch is a reading error.
- `assumptions` is the architecture's `Assumption` entity, cut down to what M2 needs: values that are neither given by a user nor produced by a rule. Examples are an assumed wire colour, or a cross-section copied from the cable.

### 2.2 Entities

| Entity              | Fields (summary)                                                                                                                                                                                                                                        | Identity                                  |
| ------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------- |
| `ComponentTemplate` | `ref {scope: global\|tenant, id, version}`, `function` (string, e.g. `"contactor"`; data, not an enum), `terminals[]` (`TerminalDef`), `contact_groups[]`, `parameters[]` (name, type, unit; no defaults that carry ratings), `status: draft\|approved` | `ref` is unique per snapshot              |
| `TerminalDef`       | `id` (unique in template), `marking` (as printed on the device, e.g. `"A1"`), `kind` (string from an open vocabulary: `power_line`, `protective_earth`, `coil`, `contact_no`, `signal_di`, …), `required: bool`, `group` (contact group id or null)     | `(template ref, id)`                      |
| `Component`         | `id`, `board_id`, `template_ref`, `designation` (v1 `Designation`, derived), `parameters` (name to Decimal-string/str), `legacy_device_id` (the v1 device it mirrors, or null)                                                                          | `id`, stable, assigned once               |
| `TerminalStrip`     | `id`, `board_id`, `designation`, `template_ref` (the terminal type, optional), `points[]`                                                                                                                                                               | `id`                                      |
| `StripPoint`        | `id`, `position` (int ≥ 1, ordered), `level` (int ≥ 1, for multi-level types), `marking` (printed number, string), `kind`                                                                                                                               | `id`. `(strip, marking, level)` is unique |
| `TerminalRef`       | either `{component, terminal}` (a `TerminalDef` id) or `{point, side: inner\|outer}`                                                                                                                                                                    | value object                              |
| `Net`               | `id`, `board_id`, `signal` (string label from data, e.g. `"L1"`, `"24V+"`, `"DI.03"`), `members[]` (`TerminalRef`), `origin: derived\|user`                                                                                                             | `id`                                      |
| `Connection`        | `id`, `net_id`, `from`, `to` (`TerminalRef`), `via: wire\|cable_core\|jumper\|busbar`                                                                                                                                                                   | `id`                                      |
| `Wire`              | `id`, `connection_id`, `number` (string or null), `cross_section_mm2` (Decimal or null), `colour` (code from a tenant colour table, or null), `source` per field (`user\|rule\|assumption`) with an `assumption_id` where it applies                    | `id`                                      |
| `CableCore`         | `cable_id` (a v1 cable), `index`, `label`, `connection_id`                                                                                                                                                                                              | `(cable_id, index)`                       |

All ids are opaque, content-free strings. Designations stay derived and are never identity, as in `designations.py` today.

### 2.3 Invariants (checked on read, and as findings on design)

Two layers, with different consequences:

**Schema invariants**: a violation means the snapshot is unreadable (`SnapshotUnreadableError`, like v1's strict validation). The server writes snapshots, so one of these in a stored row means a bug or tampering.

1. Ids are unique within their collection. `TerminalDef.id` is unique within its template.
2. Every reference resolves: `board_id` → a v1 board, `template_ref` → an embedded template, `{component, terminal}` → a `TerminalDef` of that component's template, `point` → a `StripPoint`, `net_id`, `connection_id`, `cable_id`, `legacy_device_id` → a v1 device on the same board.
3. Each `registry_lock` entry matches an embedded template, one for one, and the digest matches.
4. A connection's `from` and `to` are both members of its net, and differ.
5. Decimals are strings. Floats are refused (as `snapshot.canonical_json` already does).

**Structural findings**: the snapshot is readable but the design is incomplete or contradictory (Section 5). These are findings, not read errors, so a draft can hold them.

### 2.4 Canonical form and hashing

- Hashing does not change: SHA-256 over `snapshot.canonical_json` of the stored JSON, envelope included (ADR-2a).
- Collections with no meaningful order (`templates`, `components`, `strips`, `nets`, `connections`, `wires`, `assumptions`, `registry_lock.templates`, a net's `members`) are **sorted by id** (members by a canonical tuple) **by the writer before hashing**. The reader refuses unsorted input, so one design has one byte form.
- Ordered collections keep their order and say so: strip `points` by `position`, then `level`, and the v1 orders inside `design`.
- Nothing optional is written as an empty placeholder unless the schema requires the key. Every v2 collection key is required and may be `[]`. Absence is not a second encoding.
- A pinned-hash fixture per synthetic example (Section 3) fails on any accidental byte change.

### 2.5 Migration `pp-design/1` → `pp-design/2`

`migrate_1_to_2(payload_v1) -> payload_v2`, registered as `MIGRATIONS[(V1, V2)]`. `ORDER = (V1, V2)`, `ENVELOPED = {V2}`.

- **Pure and deterministic.** No registry lookup, clock, randomness or profile read. Ids are derived from v1 ids (for example `c:<device id>`), so the same input always gives the same bytes.
- `design` = the v1 payload unchanged.
- Each v1 device becomes a `Component` with `template_ref = legacy/<device kind>@1.0.0` and `legacy_device_id` set. The legacy templates are **frozen data shipped with the migration** (function = kind, no terminals). Their content is versioned with the migration, not with the live registry.
- `strips`, `nets`, `connections`, `wires` and `assumptions` are **empty**. The migration does not derive the `-X1` strip from `terminals.py`, because that module chooses articles (an engineering selection). Deriving it would be recalculating, which Phase 1 forbids on read.
- Use: **display and deriving a new revision only.** An old revision is approved, exported and checked for staleness from its stored v1 bytes, through the v1 path. A migrated payload is never hashed as, or exported as, the old revision. Saving a revision from it re-runs the design and produces a new v2 snapshot with a new hash (Section 7.4 of the architecture).
- Golden tests: every committed v1 fixture migrates. The result reads as strict v2. `project_v1(migrate(v1)) == v1`, byte for byte. The v1 stored hash still verifies. Migrating twice gives the same bytes.

### 2.6 Reader and domain model

- `read.py` gets a v2 branch: a strict `DesignModelV2` Pydantic schema, then the invariants in 2.3.
- `VersionedModel` gains `v2: DesignModelV2 | None`. `project` stays: for v1 it is the stored design, for v2 it is `design` (the v1 projection). Gating and stale-check code keeps one code path.
- `domain.py` gets frozen dataclasses for the new entities, under the same rules (Decimal, tuples, read-only mappings, kinds as strings). An `unmapped_v2_fields()` check mirrors the v1 one.
- **Writer flip.** `LATEST` becomes v2 only when a feature flag allows it (D-M2-3). Until then saves keep writing bare v1, as M1 does.

## 3. Configurations composed from data

No code branches on a template id or a configuration name. A configuration is made of:

1. **Templates** (Section 2.2): what a device is and which terminals it has.
2. **Connection patterns**: data that maps template terminals to net roles for one use. Example: "a feeder pattern joins the protective device's outgoing poles, the strip points and the cable cores by role". A pattern has a `ref`, a version and a status, like a template, and is locked in `registry_lock` too.
3. **Rules** (later, registered with provenance): fill wire numbers, cross-sections and colours. Until a rule is approved, these fields stay null or are recorded as `Assumption`s.

The pattern engine is generic. It instantiates components, creates strip points in order, creates one net per role per circuit, and connects members. A new configuration is new data. A test (12a "no-hard-coding proof") adds a template and a pattern **in the test alone** and gets a valid model. A static check fails if `app/design` contains a string literal that matches a registered template or pattern id.

The examples below are synthetic and invented for this document. Values marked `null` or `assumption` are deliberately not engineering values. Terminal markings and role names in them (`1`/`2`, `L1`…`T3`, `A1`/`A2`, `13`/`14`) are illustrative labels for the data shape, not a marking convention this design adopts; markings come from each template's data.

**Example A: single-phase outgoing circuits to a strip.**

```yaml
templates: [ {ref: global/mcb-1p@1, function: circuit_breaker,
              terminals: [{id: in1, marking: "1", kind: power_line, required: true},
                          {id: out1, marking: "2", kind: power_line, required: true}]} ]
pattern: global/feeder-1p@1
  roles: [line, neutral, pe]
  joins: {line: [mcb.out1, strip:+, core:1], neutral: [busbar:N, strip:+, core:2],
          pe: [busbar:PE, strip:+, core:3]}
wires: {number: null, cross_section_mm2: null, colour: null}   # awaiting rules
```

**Example B: motor feeder with a separate control circuit.**

```yaml
templates:
  [
    contactor-3p-coil@1 (L1..L3,
    T1..T3,
    A1/A2 coil group,
    13/14 contact_no in same group),
    overload-3p@1,
    psu-dc@1 (L,
    N,
    PE,
    +,
    -),
  ]
patterns: [global/motor-dol-power@1, global/coil-via-contact@1]
nets: power roles L1-L3 → T1-T3 → strip U/V/W/PE;  control roles "ctrl+", "ctrl-", "coil-cmd"
```

The coil and its contacts share a contact group, so cross-references come from data.

**Example C: a new configuration, I/O marshalling, added as data only.**

```yaml
templates: [tenant/acme-io-8di@1 (8 × signal_di, required: false; 2 × supply, required: true),
            tenant/strip-2level@1 (points with level 1/2)]
pattern: tenant/di-marshal@1
  per channel n: join io.DI[n] ↔ strip point (n, level 1); sensor supply ↔ (n, level 2)
```

This needs no change to `app/design` code. It is the held-out test case for the no-hard-coding check.

## 4. Phase 1 guarantees under v2

| Guarantee                                      | How it holds                                                                                                                                                                           |
| ---------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Server-built snapshot                          | Only the design service writes v2. A client never sends `components`, `nets` or similar as a snapshot. User edits arrive as request data and are rebuilt by the server.                |
| Hash before read                               | Unchanged: `_check_snapshot` and `_read_stored` hash the stored bytes, then call `read_snapshot`.                                                                                      |
| Approval bound to snapshot + policy + renderer | Unchanged columns. The recorded renderer version is checked against **the renderer of the snapshot's schema** (below).                                                                 |
| No silent recalculation                        | Migration on read is display/revise only (2.5).                                                                                                                                        |
| Execution export refused                       | `execution_package_incomplete` stays first and unconditional. Terminal and wire lists do not unlock it.                                                                                |
| CSV, QET, AML, PLC refused                     | Unchanged (`export_format_unprotected`) until each format's documented acceptance criteria are met. A v2 terminal or wire list is a new reason to want CSV, not a reason to enable it. |

**Renderers per schema.** `RENDERER_VERSION` becomes a mapping, `RENDERER_VERSIONS = {"pp-design/1": "2026.10.31.2", "pp-design/2": "<new>"}`. The existing constant stays as an alias for the v1 entry, so the stored value means the same thing. An approval stores the version for its snapshot's schema in `approved_renderer_version` (same column). The comparison is `approved == RENDERER_VERSIONS[schema]`. `RENDERER_BASELINE = "unversioned"` keeps matching nothing.

- **v1 renderers** are unchanged and keep exporting v1 revisions (decision 13 in the architecture doc).
- **v2 renderers** at first print `project` (the v1 projection) with the v1 code, plus new pages: terminal strip diagram and lists of terminals and wires, as **review** content only. They have their own version, so a change to the new pages does not invalidate v1 approvals, and the reverse holds too.
- The identity line adds the schema: `rules …, renderer pp-design/2:<version>`.
- `test_renderer_version.py` gets one fingerprint set per schema. v2 fingerprints come from the three Section 3 examples plus the existing four variants migrated and re-designed. The classification policy applies as it stands: wire numbers, terminal markings and strip geometry are **material content**, and when in doubt a change is material. If wire colour is ever printed as colour, colour becomes content (the policy already says so).

## 5. Validation and findings

Structural findings check only the model's own consistency. They are kept apart from engineering rules: `source: "integrity"`, and no `rule_ref`.

| Code (proposed)                 | Condition                                                                                                     | Proposed class                           |
| ------------------------------- | ------------------------------------------------------------------------------------------------------------- | ---------------------------------------- |
| `model_dangling_reference`      | Reserved. Stored snapshots are refused instead (2.3). Used for **requests** that name a missing id            | blocking                                 |
| `terminal_marking_duplicate`    | Two points of a strip share `(marking, level)`, or a template has two terminals with the same marking         | blocking                                 |
| `terminal_required_unconnected` | A `required` terminal is in no net                                                                            | blocking for approval (D-M2-6)           |
| `terminal_in_multiple_nets`     | A `TerminalRef` is a member of two nets                                                                       | blocking                                 |
| `net_signal_conflict`           | Members with different declared signals end up joined through connections (e.g. `L1` and `N`)                 | blocking                                 |
| `connection_outside_net`        | Endpoints not in the connection's net (also a schema invariant on stored rows)                                | blocking                                 |
| `net_unrealised`                | A net with two or more members and no connection path between some of them                                    | warning + confirmation                   |
| `wire_number_duplicate`         | Same number on wires of **different** nets. Whether this is an error depends on the numbering scheme (D-M2-5) | warning until decided                    |
| `wire_attribute_unset`          | Number, cross-section or colour null, or only assumed                                                         | warning + confirmation                   |
| `template_not_approved`         | Uses a `draft` template or pattern                                                                            | blocking for approval (architecture 7.5) |

`terminal_in_multiple_nets` and `net_signal_conflict` check only what the declared signals say. They do not decide whether a joined potential is electrically acceptable. That is a rule's job.

**Policy and staleness.** New codes are added to `findings_policy.classify`. v1 snapshots never produce them, so the findings fingerprint of a v1 revision is unchanged and no v1 approval goes stale. Whether adding them bumps `RULES_VERSION` is D-M2-7.

**Not invented here.** No sizing of cross-sections, colour code, wire-number format, terminal rating check, PE-continuity rule or standards clause. Each needs a registered rule with a source (architecture 8.2) and is listed in Section 7.

## 6. Delivery plan

| Slice                                   | Content                                                                                                                                                            | Acceptance tests                                                                                                                                                                                                                                                                                                                   | DB impact                                                                                                       | Risks                                                                                                       |
| --------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------- |
| **M2a** Model + migration               | `DesignModelV2`, domain dataclasses, invariants, canonical writer, `migrate_1_to_2`, frozen `legacy/*` templates, v2 reader branch. Writer flip behind a flag, off | Every v1 fixture migrates and reads as strict v2. Projection back to v1 is byte-identical. v1 stored hashes verify. Migration is idempotent and deterministic (property tests). Unsorted or duplicate-id v2 is refused. A pinned hash per example. An envelope with extra keys is refused. All Phase 1 and M1 tests pass unchanged | None: `design_snapshot` is JSONB and already holds any JSON. No Alembic revision                                | Hash churn from ordering mistakes; choosing the wrong embedded-vs-referenced template split (D-M2-2)        |
| **M2b** Templates, patterns, validation | Template/pattern data files (global scope only), generic pattern engine, structural findings, policy classes                                                       | Examples A–C build from data. The held-out template test passes. The static check finds no template id literal in `app/design`. Each finding code has a positive and a negative test. v1 findings fingerprints are unchanged                                                                                                       | None if templates ship as versioned files. A tenant-scope registry table would need a migration and is deferred | Template granularity (architecture D-3). The engine creeping into rule territory                            |
| **M2c** Renderers                       | `RENDERER_VERSIONS`, per-schema approval comparison, v2 pages (strip diagram, terminal and wire lists, marked as review), identity line with schema                | Fingerprints per schema. Changing a v2 page without a v2 bump fails. A v1 approval still counts after a v2 bump. Execution and the four refused formats are still refused for v2 revisions (explicit tests)                                                                                                                        | None (same `approved_renderer_version` column, new value format)                                                | Value-format change in that column: old rows must keep comparing correctly (test with stored legacy values) |
| **M2d** UI + flip                       | Show components, strips, nets and wires read-only on an opened revision. Show findings with "where to fix". Turn the writer flag on per environment                | v1 and v2 revisions open side by side. A revise of a v1 revision produces a v2 revision with a new hash, and the old row is byte-for-byte unchanged. Localized messages for new codes                                                                                                                                              | None                                                                                                            | Users reading terminal lists as execution data. Mitigation: draft and review marking on every page          |

**Out of scope for M2:** execution release (M8 gate), enabling CSV/QET/AML/PLC, rule registry and CalcRecords, catalogue articles on terminals, logic/PLC derivation, layout, tenant-authored templates in the UI, any calculated wire attribute, and any claim of readiness or conformity.

## 6a. Recommendation: templates in the snapshot, and the move to `pp-design/2`

**Embed the exact template definitions, and lock them by digest.** Recommended over referencing a registry by id or digest alone.

|                     | Embed definitions + `registry_lock` digests (recommended)                                          | Reference by `ref` + digest only                                     |
| ------------------- | -------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------- |
| Reproducible        | Yes: the snapshot alone reads, validates and renders the same years later                          | Only while the registry keeps every version forever, unchanged       |
| Hash covers meaning | Yes: what each terminal is is inside the hashed snapshot                                           | Only indirectly, through the digest                                  |
| Approval binding    | The approved hash binds the templates used                                                         | Depends on registry immutability, outside the approved hash          |
| Size                | Larger snapshots: each design repeats the templates it uses                                        | Small                                                                |
| Template fixes      | Never reach a saved revision silently; a new revision picks up the new version, and is re-reviewed | The same, provided the registry never edits a version in place       |
| Tenant privacy      | A tenant's templates live in its own snapshots                                                     | Needs tenant-scoped registry storage and access checks on every read |

The extra size is the cost of reproducibility, and approval integrity depends on that reproducibility. If size becomes a problem, de-duplicate by digest in storage, behind the same reader. The canonical bytes and the hash do not change.

**The move to `pp-design/2`: versioned and reproducible, in stages.**

1. **Read first.** Ship the v2 reader, `migrate_1_to_2` and the v2 renderers with writing still off, so every environment can read v2 before any v2 row exists.
2. **Write new revisions only.** Behind a per-environment flag, after M2c's renderers and fingerprints exist, new saves write v2. A stored v1 snapshot is **never** rewritten or re-hashed: it opens through the pure on-read migration for display, and exports with the v1 renderers under its own approval.
3. **Approvals stay with their revision.** A v1 approval never becomes a v2 approval. Moving a design to v2 means saving a new revision, which is re-reviewed and approved like any other. This is the same rule as the Phase 1 re-approval runbook.
4. **Renderer versions per schema.** v1 and v2 renderers each have a recorded version and their own fingerprint set. An approval binds the renderer of its revision's schema.
5. **Turning it off.** Turning the flag off stops new v2 saves. Existing v2 rows stay readable, because the reader ships first. There is no down-migration of stored snapshots.

**Approval of templates, patterns and rules.** No template, connection pattern or rule becomes usable in an approved design without the decision of a qualified engineer, recorded with who, when, the digest and the scope. Until the owner decides D-5 and D-M2-12, only `approved` global templates are usable. Tenant templates stay `draft`, and a design that uses a draft template gets a blocking finding.

## 6b. As built (M2a)

This section records what M2a actually delivers. Where it differs from the proposals above, this section describes the code; the proposals stay open for the owner.

**Architecture decisions taken**

- **Embedding and lock.** A `pp-design/2` snapshot holds the exact definitions of the templates it uses and a `registry_lock` with each template's digest (D-M2-2 as recommended). The reader checks every digest; a mismatch, a reference that does not resolve or an unknown key makes the snapshot unreadable.
- **Read before write.** The v2 reader is always on, in every environment, whatever the switch says. Every path that reads a stored snapshot (open, approve, export, the execution refusal) checks the hash over the stored JSON first and then reads it through the single reader, `read_snapshot`.
- **The write switch.** `DESIGN_SNAPSHOT_SCHEMA` (in `app/core/config.py` and `.env.example`) accepts exactly `pp-design/1`, the default, or `pp-design/2`. Any other value stops the process at startup. With `pp-design/2`, a new save or revision stores `snapshot_v2_from_v1` of the server's v1 design, and the recorded hash is the hash of that stored envelope. There is no configuration input yet, because there is no API for configurations: the v1 devices become components on their legacy templates, and there are no strips, nets, connections or wires. The switch is a per-environment setting. It is added before the v2 renderers, so Section 6a's ordering is not met yet: deployed environments should keep it at `pp-design/1` until the owner decides D-M2-3.
- **History is never rewritten.** No stored row is rewritten, re-hashed or migrated in place. Revising a v1 project with the switch on adds a new v2 revision. Earlier revisions and their approvals are unchanged, and those approvals still count. Turning the switch off stops new v2 saves; existing v2 rows stay readable.
- **Renderers.** The renderers are unchanged and are given the v1 design that a v2 snapshot holds. A v2 revision's PDF, calculations PDF, DXF and JSON exports therefore have the same content as the v1 exports of the same design; only the printed design hash differs. `RENDERER_VERSION` is unchanged. Per-schema renderer versions will come with the v2 renderers. The approval binding (snapshot hash, profile, rules and catalogue versions, renderer version, findings fingerprint) applies to a v2 revision exactly as to a v1 one.
- **Findings mapping.** The integration owner decided how v2 structural findings are stored as revision findings (`source: "integrity"`):

  | Code                            | Stored as                               | Blocks design-review approval | Execution blocker |
  | ------------------------------- | --------------------------------------- | ----------------------------- | ----------------- |
  | `template_unreviewed`           | warning, needs engineering confirmation | No                            | Yes               |
  | `out_of_execution_scope`        | warning, needs engineering confirmation | No                            | Yes               |
  | `unconnected_required_terminal` | blocking                                | Yes                           | Through approval  |
  | `wire_attribute_unset`          | warning, needs engineering confirmation | No                            | No                |
  | any code not listed             | blocking (a new check is never weaker)  | Yes                           | Through approval  |

  The reason: every legacy template is unreviewed, so blocking approval on `template_unreviewed` would make every v2 revision unapprovable while telling the reviewer nothing about the design itself. A design review covers the design; execution is a separate gate, and that gate names the codes (`template_unreviewed`, `out_of_execution_scope`) in the revision's `execution_blockers` and in the `execution_package_incomplete` refusal. This differs from Section 5 and Section 6a, which proposed blocking. The owner may still decide otherwise.

- **No effect on v1 revisions.** v2 structural findings are added only when the stored snapshot is v2. A v1 revision gets exactly the findings it always did, so its findings fingerprint, its hash and its staleness are unchanged. `RULES_VERSION` is not bumped (D-M2-7), and tests prove that every committed v1 fixture still designs to its committed bytes.
- **Printed findings.** The v1 renderers print the v1 findings only. A v2 revision's structural findings are shown when it is opened and named by the execution refusal, but they are not printed on its exports until there are v2 renderers.
- **Ids.** Component, strip, net, connection and wire ids are opaque and derived from the ids they stand for (a collection prefix and 24 hex digits of a SHA-256), so migration and composition are deterministic and an id carries no meaning.

**Restrictions that are unchanged.** An execution export is always refused with `execution_package_incomplete`, for v2 revisions too. CSV, QET, AML and PLC exports are still refused. A quotation is still a provisional estimate.

**Not done in M2a**

- No configuration API or UI: nothing yet supplies templates, patterns or bindings to a save.
- No v2 renderers: terminal plans, wire lists and connection drawings are not produced, and there are no per-schema renderer versions.
- No rules: nothing sets or checks a wire number, cross-section or colour, a terminal rating or a standards clause.
- Connection patterns are not locked in the snapshot the way templates are.
- The findings `net_unrealised`, `net_signal_conflict` and `wire_number_duplicate` are not implemented.

**Templates and execution.** Every template in use today is an unreviewed legacy template. An unreviewed template cannot support any claim that a design is ready for execution. That stays true until the owner names the qualified engineers who may review templates and the standards that apply, and those engineers record their decisions. Fire-pump and life-safety designs stay outside execution scope. Nothing in M2a states or implies compliance with any standard.

## 7. Open decisions

**New for M2**

| #       | Decision                                                             | Options                                                               | Proposed default                                                                                                                                                  |
| ------- | -------------------------------------------------------------------- | --------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| D-M2-1  | Milestone numbering                                                  | Decided 2026-10-31                                                    | M2 is this work; rule registry is M3 (Section 0)                                                                                                                  |
| D-M2-2  | Templates in the snapshot                                            | Embed full definitions; or reference with lock digests only           | **Recommended: embed + lock digests** (Section 6a); owner to confirm                                                                                              |
| D-M2-3  | When saves start writing v2                                          | Flag per environment; per tenant; all at once                         | **Recommended: read first, then new revisions only, per-environment flag after M2c** (Section 6a); owner to confirm                                               |
| D-M2-4  | Should migration derive today's `-X1` strip?                         | Empty (pure); or derive in the migration                              | Empty. Derived only when a new revision is designed                                                                                                               |
| D-M2-5  | Wire numbering semantics (QE)                                        | Per profile `WireNumbering` scheme: uniqueness scope, format, jumpers | Owner and engineering define per scheme. Until then numbers are null or user-entered                                                                              |
| D-M2-6  | Severity of unconnected required terminals and unset wire attributes | Blocking for approval or warning                                      | Blocking for required terminals, warning for attributes (owner to confirm)                                                                                        |
| D-M2-7  | Does adding structural codes bump `RULES_VERSION`?                   | Bump; or only for codes v1 can produce                                | No bump if v1 fingerprints are unchanged (test-proven)                                                                                                            |
| D-M2-8  | Cross-section rules (QE)                                             | Copy from cable; sizing rule; user only                               | Engineering to define. Until then null or assumption                                                                                                              |
| D-M2-9  | Conductor colour table (QE)                                          | Per market or tenant table, with source                               | Engineering to define. No built-in colours                                                                                                                        |
| D-M2-10 | Terminal ratings and article fit (QE)                                | Check section vs terminal range, and how                              | Out of M2 (needs catalogue and rules)                                                                                                                             |
| D-M2-11 | Jumper and busbar modelling                                          | Connection `via`; separate entity                                     | `via` on connection (revisit in M3)                                                                                                                               |
| D-M2-12 | Who may approve templates and patterns                               | See architecture D-5                                                  | A qualified engineer's recorded decision for every template and pattern; the owner names who qualifies. Until then tenant templates stay draft and block approval |

**From the architecture document that M2 depends on:** D-3 template granularity, D-4 plugin mechanism (data-only for tenants), D-5 who approves rules and templates, D-8 tenant loosening of sourced rules (needed before any wire rule), D-12 execution package contents (still refused), D-13 lifetime of v1 renderers (M2 keeps them), D-14 async threshold (larger v2 snapshots), and D-1 standard editions (any future rule source). The Phase 1 acceptance criteria for CSV, QET, AML and PLC stay as they are.
