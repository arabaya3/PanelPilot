# Responsive audit — tablet and phone

FE-013's checklist, one row per customer-facing surface.

The primary usage context is a browser on a tablet or phone next to a live
panel, not the 1440px desktop the team builds on. The acceptance criterion is
no horizontal scroll or clipped content at common tablet and phone widths.

Audited at **360px** (small phone) and **768px** (tablet). Both are real
breakpoints rather than round numbers: 360 is the narrowest Android viewport in
common use, and 768 is portrait iPad.

## What is enforced mechanically

`npm run check:responsive` fails the build on markup that cannot fit a 360px
viewport — a fixed or minimum width at or above it, or a table with neither a
scrollable container nor a stacked fallback. It runs as part of `npm run lint`.

It is a static check, deliberately. jsdom performs no layout, so a rendered
test asserting "nothing exceeds 360px" would pass whatever the markup said —
worse than no test. Real measurement belongs in a browser run; what the script
catches is the class of markup that causes overflow, before it is ever
rendered.

The script was verified against its own failure modes: a `w-[400px]` box, a
`min-w-[500px]` box and a bare `<table>` are each caught, and a table inside
`overflow-x-auto` and a `w-[200px]` box each pass.

## Screens

| Surface              | 360px | 768px | Notes                                                                                |
| -------------------- | ----- | ----- | ------------------------------------------------------------------------------------ |
| Chat transcript      | Pass  | Pass  | Fluid. User bubbles capped at 80%; `break-words` added — see below.                  |
| Chat composer        | Pass  | Pass  | `flex` row, `flex-1` input, one button visible at a time. Fits at 360 with margin.   |
| Image capture        | Pass  | Pass  | Previews were height-capped only; `max-w-full object-contain` added — see below.     |
| Diagnostic card      | Pass  | Pass  | No fixed widths. Severity badges wrap.                                               |
| Technical tokens     | Pass  | Pass  | Order codes are one unbroken word to CSS; `break-words` added — see below.           |
| PLC view — ST        | Pass  | Pass  | Code sits in `overflow-x-auto`; a long line scrolls its own container, not the page. |
| PLC view — ladder    | Pass  | Pass  | SVG is `w-full max-w-full` inside `overflow-x-auto`, so it scales rather than clips. |
| Verification console | Pass  | Pass  | `md:grid-cols-2` — stacks below the tablet breakpoint rather than squeezing panes.   |
| Language / theme     | Pass  | Pass  | Inline controls, no fixed sizing.                                                    |
| Token reference page | Pass  | Pass  | Internal, not customer-facing. Included for completeness.                            |

## Fixes made in this pass

**Image previews were height-capped but not width-capped.** `max-h-40` alone
scales a landscape photo's width proportionally, so a wide panel photograph —
4000×800 is not unusual for a cabinet shot — renders 160px tall and 800px wide
and pushes the page sideways. Now `max-h-40 max-w-full object-contain`, which
bounds both axes and keeps the aspect ratio. Four call sites.

**Order codes could not wrap.** `3RV2011-1JA10-0BA0` is one word to CSS, and a
technical token inside prose on a 360px screen pushes the line past the
viewport rather than wrapping. `break-words` breaks only when the token
genuinely cannot fit, so ordinary codes stay intact and readable.

**User message bubbles had the same problem.** The 80% cap bounds the bubble,
not the text inside it. An engineer pasting an order code or a URL — which is
exactly what happens next to a panel — overflowed it. `break-words` added.

## Out of scope

**The panel BOM table.** FE-013's edge case calls for a stacked card fallback
below a width threshold. There is no BOM table to audit: it belongs to BE-011
and FE-010, both blocked on manufacturer engineering guides that are not
available in this repository. The `check:responsive` script already refuses a
table with neither a scrollable wrapper nor a stacked fallback, so the
requirement is enforced ahead of the component that will need it — a BOM table
added later cannot merge without one.

## Repeating this

Re-run when a component gains fixed sizing, a table, or a new dense layout.
The script catches the mechanical cases; the table above is the human pass, and
its value is that each row was actually looked at rather than assumed.
