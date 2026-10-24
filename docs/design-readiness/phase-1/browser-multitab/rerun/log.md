# Multi-tab sessions: rerun on ad859b8

- API and web on ad859b8, dev DB at b2e8d5f7c3a1, web http://127.0.0.1:3000 (Next.js dev server).
- Browser: Playwright Chromium, one context with 6 tabs:
  - t0 holds the unsaved design;
  - t1–t5 are the test tabs.
- Step 6 adds a second and a third context.
- Account: a new disposable `e2e-p1-tabs-<n>@example.com`. Its credentials are only in `.test-accounts.local` here, which git ignores.
- No token or cookie value was printed. Statuses are in `observed-statuses.json`.

## Results

| Step                                                                            | Result                                                              | Evidence                       |
| ------------------------------------------------------------------------------- | ------------------------------------------------------------------- | ------------------------------ |
| 1. Open 2, 3, 5 tabs                                                            | PASS                                                                | 01, 02                         |
| 2a. Reload all at once (2/3/5 tabs)                                             | PASS: all signed in, no session_ended                               | `step2`                        |
| 2b. Raw refresh from every tab at once, then projects with each tab's own token | PASS: all 200 / 200                                                 | `step2`                        |
| 3. Reload all 6 tabs at once, twice                                             | PASS: signed in x6, refresh and projects 200 x6                     | 03, 04                         |
| 4. D1 repro, followed by an app renew with bearer                               | **PASS**                                                            | 06, `step4.X`                  |
| 4. D1 repro, followed by a cookie-only refresh                                  | **FAIL**: 401 session_ended, session revoked                        | 07, `step4.Y`                  |
| 4. D1 repro, then wait 15 s, then reload a tab                                  | **FAIL**: tab signed out, session revoked                           | 08, `step4.Z`                  |
| Rate limit: reloading 5 tabs                                                    | PASS with a caveat (see D3)                                         | 09, `rate_limit_5_tab_reloads` |
| 5. Refreshes racing a logout                                                    | PASS                                                                | 10                             |
| 6. Stale cookie replayed from a fresh context after 11 s                        | PASS: replay 401, context 1 ended, context 2 survives (200/200)     | 11, 12                         |
| 7. Unsaved design                                                               | PASS: kept through step 2, offered back after each reload, restored | 01, 05                         |

## Step 4 sequence (as before)

1. Send a cookie-only `POST /auth/session/refresh` from t1–t5 at once, with these delays:
   - t1: response held 5 s;
   - t4: response held 3 s;
   - t3: request held 2 s.
2. All five return 200.
3. t3's request rotates the token. The late answers to t4 and t1 then put the previous token, already spent, back in the cookie jar.

What happens next depends on the first refresh after the 15 s wait:

- **X: the app renews first.** Save project in t0; its first `POST /design/projects` is answered 401 by a test route. The app renews with the bearer. The refresh presents the regressed cookie and returns 200, and the server sets the live token again. After that, a cookie-only refresh returns 200, and reloading t5 leaves it signed in. The fix works on this path. The app showed "renewed. Try again." and did not retry the save by itself.
- **Y: a cookie-only refresh first.** It returns 401 session_ended. The session is revoked, so every later refresh fails, including the app's renew with the bearer in t0, and reloading signs the tab out.
- **Z: a reload first.** This is a normal user action. The refresh on page load sends no bearer, because a fresh page has no token in memory. The tab comes back signed out and the session is revoked. Tabs t1–t4 had not been reloaded; they kept showing "Signed in" until their next request.

## D1b (open): after an out-of-order regression, a reload or any refresh without a bearer ends the session

To reproduce:

1. Run the step 4 sequence above.
2. Wait 15 s.
3. Reload any tab. The tab is signed out, and every other tab of the session fails its next refresh.

Cause: the predecessor is re-issued only when the request carries the access-token proof. A page that has just loaded has no proof, so the regressed cookie is treated as a replay.

The regression requires several refreshes in flight at once. The app's Web Lock normally prevents that. Cookie-only refreshes made outside the lock, browsers without Web Locks, and network reordering are still exposed.

## D3 (minor): every tab load makes two of each design request

- Each tab load sends 2× `GET /design/projects`, 2× `GET /design/company-settings` and 2× `GET /design/markets`. This is probably React StrictMode's double effect in the dev server; production was not checked.
- With a fresh rate-limit window, 5 rounds of reloading 5 tabs (30 design calls per round) gave no 429. Coordination kept refreshes to 2–3 per round.
- There is no retry storm on 429 any more: each tab still makes only 2 calls.
- Earlier, in a window already used by the step 1–3 reloads and by designing and saving, rounds 2–3 of a 5-tab reload test did hit `429 rate_limited`. The limit is 240 calls per 300 s per IP, so about 8 rounds of 5-tab reloads in 5 minutes exhaust it.

## Method notes

- The 401 that drives the app's renew was injected with `page.route` on the first `POST /design/projects`, the save in t0.
- Tabs with unsaved work were reloaded with CDP `Page.reload`, and the beforeunload prompt was accepted.
