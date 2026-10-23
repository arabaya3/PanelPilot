# Phase 1 backend notes

Decisions the backend agent took while implementing `contract.md`.

- **Strict inputs.** `StrictModel` (`extra="forbid"`) is used for every model a design request, a draft export or a save carries (info, revisions, boards, supply, loads, conditions, the whole `DesignProject`, pricing, save, revise, approve, revision export). The free-form `profile` dict stays a dict: `profile.load_profile` refuses unknown settings itself. A 422 for an unknown field keeps FastAPI's own shape (`detail[].type == "extra_forbidden"` with `loc`), the shape the web app already reads for 422s. Every revision `request` is stored as `model_dump(mode="json")`. A read-only sample of the dev DB showed only known keys, so stored requests still validate (see `test_a_stored_request_shape_still_opens`).
- **409s** use a new `ConflictError` in `app/core/errors.py`. It returns the existing envelope `{error, detail, code, params}`. Params are strings, so lists are comma-joined.
- **Profile hash** (lead's decision): it is taken from the profile the design used, which is the revision's own `request.profile` resolved over the default profile. Company settings do not feed the design, so changing them does not make a revision stale. A change to the default profile does.
- **Save when the design is refused.** The revision is still saved, so no entered work is lost. It has no snapshot and carries a blocking `design_refused` finding. Its status is `not_validated`.
- **Approval.** Approving a stale revision is also refused (`revision_stale`). An approval that binds to a snapshot which can no longer be exported would be useless.
- **Concurrent revisions.** `revise_project` locks the project row (`FOR UPDATE`) before it numbers the new revision. Any remaining IntegrityError becomes 409 `revision_conflict`.
- **Integrity Iz.** Iz comes from the request's conditions for a saved revision and from the board's `cable_conditions` note for a draft. When neither is available, the check uses the largest tabulated capacity, an upper bound, so it never blocks a cable that might actually hold.
- **Marks.** JSON is wrapped in `{export_status, findings, project}`. In CSVs, the BOM stays first and the `#` comment line follows it. The findings pages are appended after the drawing set; the frame's "n of N" counts the drawing pages only.
