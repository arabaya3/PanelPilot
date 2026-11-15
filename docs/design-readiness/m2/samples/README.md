# M2 review sample exports (synthetic)

**Synthetic data only. Review content only, NOT FOR EXECUTION.** Every template and pattern in these files is unreviewed; no qualified engineer has reviewed them, and nothing here states or implies compliance with any standard. Markings, signals, wire numbers, colours and assumption statements are illustrative labels from the committed test configurations, not conventions or engineering values.

Each sample is a configured `pp-design/2` design of a committed synthetic case (`apps/api/app/tests/design/model/configurations/`), rendered as an **unapproved** revision 02 (the draft mark) through the same functions a revision export uses, under renderer `2026.10.31.v2-3`:

| Case                                     | Drawing PDF                     | Terminal schedule                         | Wire schedule                         |
| ---------------------------------------- | ------------------------------- | ----------------------------------------- | ------------------------------------- |
| `pe_n_distribution` (added as data only) | `pe_n_distribution.drawing.pdf` | `pe_n_distribution.terminal_schedule.pdf` | `pe_n_distribution.wire_schedule.pdf` |
| `io_marshalling` (two-level I/O strip)   | `io_marshalling.drawing.pdf`    | `io_marshalling.terminal_schedule.pdf`    | `io_marshalling.wire_schedule.pdf`    |

`summary.json` is read back from the files by `generate.py`: page count, the labels found on every page (`DRAFT — NOT FOR EXECUTION` and, on schedules, `REVIEW SCHEDULE — NOT FOR EXECUTION`), whether the design hash and renderer version are printed, which structural finding codes are listed, and how many value cells show each source (`[user]`, `[data]`, `[assumption …]` with its statement, `[unresolved]`; a schedule's legend is not counted).

What to look for:

- every page carries the mark; a schedule's first page names the revision, the full design hash, the schema, rules and renderer;
- the wire schedule of `pe_n_distribution` shows all four sources: a `user` number, a `data` number, an `assumption` colour with its statement, and `unresolved` where the model holds no value;
- every structural finding is listed with its severity, whether it is unresolved, whether it blocks approval, whether it is an execution blocker, and whether it needs engineering confirmation; the drawing PDF's findings sheet lists them too.

Regenerate (from `apps/api`, with the API's Python environment and settings variables):

```sh
python ../../docs/design-readiness/m2/samples/generate.py
npx prettier --write ../../docs/design-readiness/m2/samples/summary.json
```
