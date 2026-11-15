# Browser re-check — auth, commit ec399e9 (2026-10-24)

Playwright (Chromium), local stack. Disposable account `e2e-p1-auth2-<n>-a@example.com` (credentials only in `.test-accounts.local`, git-ignored). No tokens or cookie values recorded.

| #   | Step                                                       | 127.0.0.1:3000    | localhost:3000   |
| --- | ---------------------------------------------------------- | ----------------- | ---------------- |
| 1   | Sign-up / sign-in, cookie                                  | PASS (signup 201) | PASS (login 200) |
| 2   | Design, rev 01, edit, rev 02, unsaved edit                 | PASS              | PASS             |
| 3   | Reload: session restored, unsaved work offered             | PASS              | PASS             |
| 4   | Approve rev 02 as "Eng. Final Test", no "@"                | PASS              | –                |
| 5   | PDF stamp; execution + CSV refused                         | PASS              | –                |
| 6   | Sign-out: token replay 401, refresh 401, reload signed out | PASS              | –                |
| 7   | Two-tab concurrent refresh                                 | PASS (200 / 200)  | –                |
| 8   | Arabic enclosure note LTR runs                             | PASS              | –                |

## 1. Cookie

- 127.0.0.1: `pp_refresh`, domain 127.0.0.1, Path=/api/v1/auth/session, HttpOnly, SameSite=Strict, Secure=false (http dev). Persistent (~1 year expiry).
- localhost: `pp_refresh`, domain localhost, same attributes. Screenshot `01-localhost-signed-in.png`.

## 2–3

- 127: POST /design/project 200, revisions 1 (rev 01) and 2 (rev 02, power 2→3 kW) saved; unsaved edit "Sockets hall UNSAVED". `02-127-revisions-unsaved.png`.
- Reload → "Signed in." + "Unsaved work was kept / Restore unsaved work / Discard it"; Restore brought back "Sockets hall UNSAVED". `03-127-after-reload.png`.
- localhost: "Recheck L" rev 01/02, unsaved "Lights UNSAVED"; reload → signed in + restore offered; restore gives "Lights UNSAVED" / 1.5 kW. `02-localhost-revisions-unsaved.png`, `03-localhost-after-reload.png`.

## 4. Approval

- POST .../revisions/2/approval 200: approved_by "Eng. Final Test", approver_account_recorded true, approver_name null, export_status review_approved.
- UI: "Approved by Eng. Final Test, 2026-10-24 — Approved while signed in as a team member."
- Full GET project JSON (5898 bytes): no "@", no "example.com". Page text: no "@". `04-127-approved.png`.

## 5. Export

- `downloads/rev02-drawing-set.pdf` (10 pages), pdfplumber: "REVIEW APPROVED — STUDY, NOT FOR EXECUTION" ×20, "Eng. Final Test" ×30, footer "Recheck A, revision 02, approved by Eng. Final Test on 2026-10-24, design 61c0e6926182, rules 2026.10.23.1", "Design rules software defaults…", DRAFT ×0, "@" ×0.
- Request execution export → 409 `execution_package_incomplete` (missing wiring, control schematics, orderable parts, mechanical layout); UI lists the four blockers. `05-127-export-refused.png`.
- devices_csv via API → 409 `export_format_unprotected` (reason draft_mark_not_persistent). Server `detail` is English regardless of Accept-Language; the UI localizes from the code: Arabic "غير متاح بعد: لا تستطيع هذه الصيغة الإبقاء على تحذير المسودة ظاهرًا بعد استيرادها…", CSV buttons disabled. `05-127-ar-csv-reason.png`.

## 6. Sign-out

- Captured access token: GET /design/projects 200 before. Sign out → POST /auth/session/logout 204, pp_refresh cookie removed.
- Replay token → 401. POST /auth/session/refresh → 401. Reload → "Trial workspace", not signed in. `06-127-signed-out-reload.png`.
- Note: after sign-out the page's automatic POST /auth/trial returned 429 twice (rate limit from earlier runs); the UI still showed the trial notice.

## 7. Grace window

- Two tabs (same context), Promise.all of POST /auth/session/refresh with `X-PanelPilot-CSRF: 1`: 200 and 200. A follow-up refresh: 200.

## 8. Arabic

- Drawings tab, "ما افترضه التصميم": product name and order code each in `<bdi dir="ltr" class="inline-block" data-testid="note-ltr-run">`; glyph x-positions strictly increase left-to-right on one line for both "ABB Mini Center compact GDMS332 RX" and "2CVA250001P0001". `08-127-ar-enclosure-note.png`.

## Observations (not defects)

- Sign-in/sign-up fields were pre-filled with an older run's account — browser autofill from the persistent Playwright profile, not app code (`sign-in-form.tsx` only sets autoComplete).
- Signing in on localhost with a trial present shows the "trial's projects stay with the trial" notice first ("Sign in anyway").
- Review-panel findings text (hidden tab) uses Unicode isolates rather than `<bdi>`; fine.
