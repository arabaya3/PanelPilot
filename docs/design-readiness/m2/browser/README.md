# M2 browser and export verification (synthetic)

An independent verifier, which wrote none of the code, ran the production build (`next build`, standalone server) against the API on a disposable database. It ran once with `DESIGN_SNAPSHOT_SCHEMA` unset (`pp-design/1`) and once with `pp-design/2`, on commit `a2e6d642567d28e97390c0c8cf8d5dc84ed2c442`.

- **Data:** synthetic accounts and the committed synthetic configurations `pe_n_distribution` and `io_marshalling`.
- **Results:** `results.md` (7 of 7 checks pass). It holds one low-severity defect, D1: the execution notices did not name unreviewed definitions. D1 was fixed afterwards in `552fa74`.
- **Images:** screenshots are reduced JPEG copies.
- **Downloads:** `downloads/` holds the exported PDFs exactly as the browser received them. The schedules and drawing are review only, NOT FOR EXECUTION.

Account credentials, server logs and the full-size screenshots are kept out of the repository.

The verification commit differs from the final head in two ways:

- the requiredness rule: an undocumented `required: false` is now unresolved. The sample configurations document their sources, so their output is identical.
- the D1 fix.
