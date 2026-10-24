# Multi-tab sessions: real-browser run

- Commit a1bff09, web http://127.0.0.1:3000, run on 2026-10-24.
- Browser: Playwright Chromium. One context with up to 5 tabs on /design, sharing one cookie jar. Step 6 also uses a second and a third context.
- Account: a disposable `e2e-p1-tabs-<n>@example.com`. The credentials are only in `.test-accounts.local`, which git ignores.
- No token or cookie value was printed. A cookie is identified only by the first 4 bytes of SHA-256 of its value.
- Statuses are in `observed-statuses.json`.

## Results

| Step | Result | Evidence |
|---|---|---|
| 1. 2, then 3, then 5 tabs signed in | PASS | 01-signed-in.png, 03-tab5-signed-in-after-step2.png |
| 2a. App renewal: all tabs reloaded at once (2/3/5 tabs) | PASS: all signed in, no session_ended | observed-statuses.json `step2_round1` |
| 2b. Raw `POST /auth/session/refresh` with the CSRF header from every tab at once | PASS: all 200. `GET /design/projects` with each tab's own token: all 200 (round 2) | `step2_round2_after_rate_window` |
| 3. All tabs reloaded at once, twice | PASS: signed in x5 both times, then refresh and projects 200 x5 | 08-step3-reload1-tab1.png, 09-step3-reload2-tab1.png |
| 4. Delayed, out-of-order refresh responses | **FAIL** (see defect D1). Delaying only requests passes. | 06-step4-tab1-after-out-of-order.png, `step4_delayed_out_of_order` |
| 5. Refreshes in 3 tabs racing a logout in a 4th | PASS: logout 204, the cookie was cleared, every refresh afterwards 401, the race's access tokens 401, reloaded tabs signed out | 11-step5-tab2-signed-out-after-race.png |
| 6. Replay of a stale cookie from a fresh context, more than 10 s after rotation | PASS: replay 401 session_ended. Context 1 ended (access token 401, refresh 401, signed out). Context 2 still signed in (refresh 200, projects 200). | 12a-step6-ctx2-still-signed-in.png, 12b-step6-ctx1-session-ended.png |
| 7. Unsaved design edit survives steps 2–3 | PASS: the field kept the edit through the raw refreshes. A reload showed the beforeunload prompt, then the "Unsaved work was kept" banner. Restoring brought the edit back. | 02-, 04-, 07-step2b-, 10-step7-draft-restored.png |

## D1: an out-of-order Set-Cookie moves the jar back to a spent token, and the session is revoked as a replay

Steps to reproduce (5 tabs in one context, signed in):

1. Use `page.route` on `**/api/v1/auth/session/refresh`:
   - t1: `route.fetch()` straight away, then hold the response for 5 s before `fulfill`.
   - t4: the same, holding the response for 3 s.
   - t3: hold the request for 2 s, then `continue`.
2. Send `fetch('/api/v1/auth/session/refresh', {method:'POST', headers:{'X-PanelPilot-CSRF':'1'}})` from all 5 tabs at once. All 5 return 200.
3. Remove the routes and wait 15 s. Refresh from any tab: **401 session_ended**, and every tab is signed out.

Seen twice: run 1 and run 2B. Cookie trace from run 2B:

- t0 and t2 present 9e33 and are both answered with 9a84: one rotation and one reissue in the grace window. Correct.
- t3 is sent at 2 s with the jar at 9a84. It rotates the token to ceaf.
- t4 (at 3.2 s) and t1 (at 5.2 s) deliver their earlier answers late. Their Set-Cookie (9a84) overwrites ceaf, so the jar holds the spent 9a84.
- About 21 s later the jar presents 9a84, which was spent more than 10 s before. `_end_session_on_replay` revokes the session.

Control (run 2A): with only the requests delayed, all rotations happen in order and the refresh 15 s later succeeds.

What this means:

- One honest browser can sign itself out. Concurrent refreshes whose responses arrive in a different order from how the server handled them can move the cookie back to a token that was already spent. The server then treats that token as a replay and ends the session.
- The app's own Web Lock coordination (`createRefreshCoordinator`) normally allows only one refresh in flight per browser, which avoids this. Cases it does not cover:
  - refreshes made outside the lock;
  - browsers without Web Locks;
  - real network reordering across connections.
- One possible fix: reissue the live token, instead of treating it as a replay, when the presented token belongs to the live session's recent chain and was spent recently. Keeping the lock is still required.

## Other observations (not defects in the auth flow)

- **Rate limit.** The design limit (240 requests per 300 s per IP) was used up after a few simultaneous 5-tab reloads, because each tab load makes several `GET /design/projects` calls and keeps retrying on 429. The first-round projects checks returned 429 `rate_limited`. All were re-run after the window cleared and returned 200. A user with many tabs open could hit this limit.
- **Logout in step 5.** The logout was a raw fetch, so it did not go through the app. Tab t0 was not reloaded, and its UI went on showing "Signed in" even though its credentials were already refused.
- **Draft after step 4.** Once the session ended in step 4, tab t0's draft was gone and the page showed the trial workspace. This is consistent with sign-out clearing the account draft, but it was not tested separately.
- **beforeunload prompts.** To reload a tab with unsaved work, the run used CDP `Page.reload`, and the prompt was accepted. A real user would choose "Leave".
