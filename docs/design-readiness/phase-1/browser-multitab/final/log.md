# Multi-tab sessions: final run on e9c14c3

- Web http://127.0.0.1:3000 (Next.js dev server), Playwright Chromium.
- Context M holds 6 tabs:
  - t0 has the unsaved design;
  - t1–t5 are the test tabs.
- Context D is a second session of the same user, used for (d).
- Account: a new disposable `e2e-p1-tabs-<n>@example.com`. The credentials are only in `.test-accounts.local` here, which git ignores.
- No token or cookie value was printed. Cookies are identified by the first 4 bytes of their SHA-256.
- Statuses are in `observed-statuses.json`.

## Results

| Step                                                                                        | Result                                                                                               | Evidence                                            |
| ------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------- | --------------------------------------------------- |
| 1. Open 2, 3, 5 tabs                                                                        | PASS                                                                                                 | 01, 02                                              |
| 2. Simultaneous reload, then raw refresh from every tab at once, then projects (2/3/5 tabs) | PASS: all signed in, refresh 200, projects 200, no session_ended                                     | `step2`                                             |
| 3. Reload all 6 at once, twice                                                              | PASS: signed in x6 both times                                                                        | 03, 04                                              |
| (a) D1 sequence, then a cookie-only refresh 15 s later                                      | **PASS: 200**, with both a young token and one older than 5 min                                      | `young_token_run.a`, `token_older_than_5_min_run.a` |
| (b) D1 sequence, wait 15 s, then reload a tab first                                         | **PASS: stays signed in.** The load-time refresh sent a borrowed bearer and got 200.                 | 06, 10                                              |
| (c) Reload all tabs after (a)                                                               | **PASS: signed in x6**                                                                               | 05, 09                                              |
| (d) Stale-cookie replay after a genuine supersede                                           | **PASS**: replay 401 session_ended; only session D ended; context M survived                         | 07, 08                                              |
| (e) 3 refreshes racing a logout                                                             | **PASS**: logout 204, the race's tokens 401, cookie cleared, every refresh 401, reload signed out x6 | 11                                                  |
| (f) Unsaved work                                                                            | **PASS**: kept through step 2, offered back after each full reload, restored                         | 01, 03, 04                                          |

## Timing for (d)

- Session D signed in at 13:34:43Z.
- At 13:40:22Z (5 min 39 s, past the 5-minute no-rotation window) a refresh rotated the cookie from b12db4c2 to 3289aa5e. That is a genuine supersede.
- 11 s later the old value was replayed from a fresh context, with the CSRF header and a trusted Origin. Result: 401 session_ended.
- Session D: refresh 401, reload signed out.
- Context M: refresh 200, all 6 tabs signed in.

## What the D1 sequence did

- **Young token:** no request minted a new token, and the jar stayed on 19f824bb, so there was nothing to regress.
- **Token older than 5 min:** exactly one rotation (b326aeb8 to 8ed1b0a7). Every answer set 8ed1b0a7, including t3's request held for 2 s and the late answers to t1 and t4, so the jar did not regress.

## Observations (not defects in the auth flow)

- **Shared browser and API.** During this run the Playwright browser also had a tab on 127.0.0.1:3100/design that I did not open, so another client was probably using the same browser and API.
  - Several `GET /design/projects` checks returned 429, from the per-IP design limit that client shares. I used refresh status as the auth evidence.
  - My setup step closed the browser's other non-default contexts before creating context D. Any context belonging to that other client would have been closed too.
- **Design calls per tab load.** Each tab load still makes 2× `GET /design/projects`, probably React StrictMode's double effect in the dev server. This is unchanged from the rerun.
- **Restore delay.** At the first probe, 5 s after the first reload, t2–t4 were still restoring. They were signed in at every later probe.
