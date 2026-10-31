Status: M3 foundation, built on M2 (draft PR #209). It describes what is built in this milestone and what is not. This document makes no claim of compliance, conformity with a standard, or engineering readiness. Nothing in M3 makes a design ready for execution.

# M3: rule registry and server-owned review decisions

## 1. Scope

M3 adds three things, and only these:

1. **A versioned rule registry** (`app/design/rules/`), pure and framework-free: what a rule is, how it is identified and digested, what it applies to, what it takes and gives, and how an evaluation of it is recorded so it can be reproduced.
2. **Server-owned review decisions** (`app/domain/engineering_reviews.py`, tables `engineering_reviewer_grants`, `engineering_review_decisions`, `global_engineering_review_decisions`): who may record a decision on a template, a connection pattern or a rule, and the append-only record of what they decided, bound to the exact digest of what they reviewed.
3. **Anchoring** (`app/design/model/anchoring.py`): a stored v2 design's template and pattern review status counts only as far as a server-side decision supports it.

M3 ships **no engineering rule content**. The shipped registry is empty (`app.design.rules.registry.SHIPPED == ()`) and no evaluator is shipped. Describing the existing calculation functions as registry entries is left to a later step (Section 9, O-9), because a faithful description of their inputs and outputs is itself engineering work that needs review. Test rules are synthetic, carry the scope `synthetic`, and compute nothing of engineering meaning.

## 2. Entities

| Entity                 | Where                                 | What it holds                                                                                                                                                                                                                                            |
| ---------------------- | ------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `RuleVersion`          | `rules/registry.py`                   | `ref` (`<scope>/<id>/v<version>`, no `@`), title, evaluator id and version, typed inputs and outputs (units as data), applicability, provenance, declared status (`draft` or `unreviewed` only), `synthetic` flag                                        |
| `Port`                 | `rules/registry.py`                   | name, kind (`decimal`, `integer`, `boolean`, `text`), unit (a string or none; never converted)                                                                                                                                                           |
| `Provenance`           | `rules/registry.py`                   | author, created, source documents **by reference only**, and a plain statement. Absent values stay absent; nothing is filled in                                                                                                                          |
| `Applicability`        | `rules/registry.py`                   | predicates over model data only (`eq`, `ne`, `in`, `present`, `absent` on a dotted path); all must hold                                                                                                                                                  |
| `Evaluator`            | `rules/evaluation.py`                 | a registered id and version and a pure function from typed inputs to typed outputs                                                                                                                                                                       |
| `EvaluationRecord`     | `rules/evaluation.py`                 | rule ref and digest, evaluator id and version, input digest, output digest, mode, the status the rule had                                                                                                                                                |
| Reviewer grant         | `engineering_reviewer_grants`         | tenant, account, `grant` or `revoke`, the qualification statement as the owner supplied it, who granted it, when                                                                                                                                         |
| Review decision        | `engineering_review_decisions`        | tenant, subject kind (`template`, `pattern`, `rule`), subject ref, subject digest, decision (`approved`, `rejected`, `withdrawn`), reviewer account, the reviewer's qualification statement as recorded on their grant, standards as given, reason, `independence_checked`, time |
| Global review decision | `global_engineering_review_decisions` | the same without a tenant; the reviewer is a label and a qualification statement the operator enters as supplied by the owner, plus who recorded it                                                                                                      |

**Digests.** Every digest is the SHA-256 of canonical JSON (`app.design.snapshot.sha256_of`). A rule's digest covers every field of its version. A template's or pattern's **subject digest** (`anchoring.definition_digest`) is the digest of its canonical definition with the review record set to the unreviewed one, so the digest names the content reviewed, not the review. M2's `registry_lock` digests are unchanged.

## 3. Invariants

- A rule's status in code is `draft` or `unreviewed`. `approved`, `rejected` and `withdrawn` exist only as the result of a server-side decision (`registry.effective_status`).
- A decision is bound to one digest. Changing a definition changes its digest, and its earlier decisions no longer apply to it.
- Decisions and grants are append-only: no update, no delete, refused both by the ORM (`before_update`, `before_delete`) and by database triggers (per row on `UPDATE` and `DELETE`, per statement on `TRUNCATE`). A grant's granting account is a foreign key to an account of the same tenant. Decisions list newest first by recording time, then by insertion order (`seq`). A newer decision for the same scope, kind, ref and digest supersedes the older one; both stay.
- The effective status of a subject in a tenant combines the current tenant decision and the current global decision for that exact digest: `rejected` if either rejects, else `approved` if either approves, else `unreviewed` (`withdrawn` or no decision). A tenant cannot loosen a global rejection.
- An evaluation of a rule whose effective status is not `approved` is allowed only in **draft mode**. Its result is labelled `DRAFT EVALUATION`, flagged `draft_evaluation` and `not_engineering_guidance`, has no path into a stored design, and cannot be described as approved. A rejected or withdrawn rule is not evaluated at all, not even in draft mode (`rule_not_evaluable`). The status is never taken from the caller: `evaluation.evaluate` and `evaluation.reproduce` derive it from the server's decisions (`ReviewAnchor`, `evaluation.rule_status`), and `reproduce` refuses a record whose recorded status the decisions no longer give (`rule_status_changed`).
- Approved status changes findings only (`template_unreviewed` and `pattern_unreviewed` are no longer raised for that definition). It never makes anything execution-ready: execution export and restricted formats stay refused, and `execution_package_incomplete` stays.
- `DESIGN_SNAPSHOT_SCHEMA` still defaults to `pp-design/1`.

## 4. Authorization

| Action                                      | Who                                                                                                                    |
| ------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------- |
| Designate or revoke an engineering reviewer | The tenant's recorded owner only (not an admin)                                                                        |
| Record a tenant-scope decision              | An active account holding `review_engineering_definitions`                                                             |
| Hold `review_engineering_definitions`       | Only an account with a current grant from the owner. No role grants it, the owner and admins do not hold it implicitly |
| List decisions for a subject                | Any active account of the tenant (its own decisions and global ones)                                                   |
| List designated reviewers                   | The owner or an admin                                                                                                  |
| Record a global decision                    | An operator, through the worker command `record-global-review`; there is no web path                                   |

The owner may designate themselves. That does not make a review independent: M3 does not establish that any decision is an independent review. What the server enforces is narrow. A reviewer may not decide on a tenant template or pattern they authored, where the server knows the author: the accounts that saved a revision of the tenant holding that exact definition (ref and digest). Such a decision is refused 403 `engineering_review_self_review`. Where no author is known (a `global/` definition, a rule, a tenant definition no saved revision holds, and every global decision), the decision is recorded with `independence_checked: false`, and every decision output says in `independence`, in plain words, whether independence was checked and what that check covered. Whether an owner or author may review at all stays open (O-10).

A tenant decision counts only while its reviewer still holds the owner's designation and an active account. A revocation or a deactivation stops their decisions counting (they stay recorded). `GET /decisions` shows each one's `reviewer_standing` (`designated`, `revoked`, `inactive`, or `operator` for a global decision), and `current` marks only a decision that counts. Revisions whose findings relied on such a decision become `revision_stale` (`review_decisions`). A global decision takes a `global/` ref only, so it never reaches a tenant definition with the same ref. The `synthetic/` rule scope (tests only) is refused for every recorded decision.

The designation records the owner's statement of the engineer's qualification verbatim; the server does not verify it and never invents one. The permission is checked against the database under the same row locks as every other permission (`permissions.require`): a revocation and a decision serialise on the account row.

## 5. Audit

Every row says who, when, what and why: the reviewer account (or label for a global decision), the qualification statement in force when they decided, the standards they named (as given, never added to), the reason, and the time. Grants record who granted or revoked. Nothing is overwritten, so the history of a subject is the list of its rows, newest first (`GET /engineering-reviews/decisions`).

## 6. Anchoring M2 definitions

M2's interim rule was: since no review could be recorded, any stored status other than `unreviewed` was forged, and the design was refused (`snapshot_integrity_failed`, reason `review_not_issued`). M3 replaces it with a server-side record:

- When a stored v2 design is read (`design_projects._read_stored`, `_check_snapshot`), each template's and pattern's review is replaced in memory by the **anchored** review for its subject digest in the revision's tenant: the effective status from Section 3, with the reviewer named by the decision's record id (`server-record:<scope>:<id>`), its date and standards.
- A stored review other than the unreviewed one must equal the anchored review exactly. If it does not (no matching record, or a different status, reviewer, date or standards), the design is refused as before (`review_not_issued`). With no decisions recorded, this is exactly M2's rule.
- Saves still write only `unreviewed` (`configuration_claims_review`), and a save's findings are computed on the anchored model, so a revision saved after a decision does not read as stale.
- The anchored model is never written back; `registry_lock` digests are those of the stored content.
- A new decision changes the findings classification of revisions saved before it, so those revisions read as stale (`revision_stale`, part `review_decisions`, also in `approval_changed`) and are saved again. Merged with M2's `findings_mismatch` rule: a difference confined to the review codes (`template_unreviewed`, `template_rejected`, `pattern_unreviewed`, `pattern_rejected`) is stale only if the stored review findings are those of the stored design under some set of decisions (for each definition, every place it is used, one status, or none), and a definition stored as approved (no review finding) or rejected had that decision recorded for its exact digest at some time, in the tenant or globally, by anyone; any other difference, or review findings no decisions could give, is `snapshot_integrity_failed` / `findings_mismatch`. `test_findings_fingerprint.py` also checks that review status decides no other code. Proved by `test_a_review_decision_since_the_save_makes_the_revision_stale_not_altered`, `test_a_tampered_findings_row_is_still_an_integrity_failure`, `test_dropping_a_templates_review_findings_with_no_decision_is_an_integrity_failure` and `test_a_revocation_makes_revisions_saved_under_the_reviewers_decisions_stale`. A definition's content edited outside the application (locks and hash recomputed) after its approval is therefore an integrity failure, since no decision exists for the new digest. That is deliberate: an approval of a revision is never carried over a change in what its definitions' reviews say.

## 7. Reproducibility

An evaluation is reproducible from its record: the rule is found by ref and checked against the recorded digest; the evaluator by id and version; the inputs by their digest. Inputs and outputs are canonical (decimals as normalised strings, no floats, declared units checked, unknown or missing ports refused), and evaluators are pure, so the same inputs give the same output digest on every run and machine. `evaluation.reproduce` re-runs a record and reports whether the output digest matches.

## 8. API

| Method and path                                            | Body or query                                                                                             | Answers                                                                          |
| ---------------------------------------------------------- | --------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------- |
| `GET /api/v1/engineering-reviews/decisions`                | `subject_kind`, `subject_ref`                                                                             | 200 list, newest first; 422 for a malformed kind or ref                          |
| `POST /api/v1/engineering-reviews/decisions`               | `subject_kind`, `subject_ref`, `subject_digest`, `decision`, `standards`, `reason` (unknown keys refused) | 201; 403 `engineering_review_not_permitted`; 422 strict                          |
| `GET /api/v1/engineering-reviews/reviewers`                |                                                                                                           | 200 current grants; 403 for a non-manager                                        |
| `POST /api/v1/engineering-reviews/reviewers`               | `member_id`, `qualification_statement`                                                                    | 201; 403 `reviewer_designation_owner_only`; 404 for an account not in the tenant |
| `DELETE /api/v1/engineering-reviews/reviewers/{member_id}` |                                                                                                           | 204; 403; 404                                                                    |

## 9. Migration

Migration `d9e4b7c2a5f1` (revises `c5d1e8a3f7b2`, single head) creates the three tables, their indexes and checks, and a trigger function `pp_refuse_append_only_change` that refuses `UPDATE` and `DELETE` on them. No existing row is changed. Downgrade drops the triggers, the function and the tables; with them, any recorded decision is gone and stored designs read under M2's rule again.

## 10. Out of scope

Rule content of any kind; registry entries for existing functions; `rule_ref` on findings; rules in `registry_lock`; filling wire numbers, cross-sections, colours or terminal ratings; recording template or pattern authorship; a review UI; any execution-readiness gate (M7).

## 11. Open engineering and owner decisions

| #    | Decision                                                                           | Status                                                                      |
| ---- | ---------------------------------------------------------------------------------- | --------------------------------------------------------------------------- |
| O-1  | Who is a qualified electrical engineer for PanelPilot, globally and per tenant     | Owner to designate; nobody is designated in shipped data                    |
| O-2  | Which standards and editions apply, per market                                     | Open (QE); decisions record standards only as the reviewer gives them       |
| O-3  | Wire numbering scheme                                                              | Open (QE); M2 values stay empty or assumed                                  |
| O-4  | Conductor cross-sections                                                           | Open (QE)                                                                   |
| O-5  | Conductor colours                                                                  | Open (QE)                                                                   |
| O-6  | Terminal ratings                                                                   | Open (QE)                                                                   |
| O-7  | Jumpers and busbars                                                                | Open (QE)                                                                   |
| O-8  | Fire-pump and life-safety controllers                                              | Out of execution scope until standard, edition and controller are confirmed |
| O-9  | Whether and how to describe the existing calculation functions as registry entries | Open; needs a reviewed description of their inputs and outputs              |
| O-10 | Whether an author or owner may review their own definition, and how authorship is recorded | Open (owner). M3 refuses only a reviewer who saved a revision holding that tenant definition; elsewhere `independence_checked: false` says independence was not checked. No decision is presented as an independent review |
| O-11 | Whether a tenant decision may narrow a global approval only, or also approve alone | M3: either approves; a rejection anywhere wins. Owner to confirm            |
| O-12 | What execution readiness requires, beyond review                                   | Unchanged: blocked (`execution_package_incomplete`) until M7                |
