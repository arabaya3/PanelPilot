'use client';

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react';

import {
  refreshSession,
  signOutEverywhere as signOutEverywhereOfServer,
  signOutOfServer,
  type RefreshOutcome,
} from '@/lib/auth';

/**
 * The signed-in account, shared by every page under the app shell.
 *
 * The access token is React state in the root layout: memory only, never
 * storage. The refresh token is not held here at all; it is the HttpOnly
 * session cookie the API sets (see `lib/auth.ts`). So a reload loses the
 * access token but not the account: with `restore`, the provider asks for a
 * new access token with the cookie before rendering the pages, and they start
 * signed in to the same account they were in.
 *
 * `expired` is distinct from `signed-out`: a page that was working for an
 * account must ask for that account again, not carry on as someone else (a
 * trial) and save the engineer's work where their account cannot see it.
 */
export type AccountTokens = { accessToken: string };

export type AccountState =
  | { status: 'signed-out' }
  | { status: 'signed-in'; tokens: AccountTokens }
  | { status: 'expired' }
  /** Asking the server, on load, whether this browser has a session. */
  | { status: 'restoring' };

// A renewal that timed out is reported as `failed`: the token is kept and
// the next 401 tries again, exactly as when offline.
export type RenewOutcome = Exclude<RefreshOutcome['kind'], 'timed-out'> | 'signed-out';

export interface AccountSession {
  state: AccountState;
  /**
   * The sign-in could not be restored on load in time. The page asks for the
   * account (`expired`) rather than starting a trial over what may be a
   * signed-in identity, and says why.
   */
  restoreTimedOut: boolean;
  signedIn: (tokens: AccountTokens) => void;
  /** Ask for a new access token after a 401; `expired` leaves the account signed out of. */
  renew: () => Promise<RenewOutcome>;
  signOut: () => void;
  /**
   * End every sign-in of the account, on every device. Signs this page out
   * only once the server has done it: on `failed` the page stays signed in,
   * so the engineer is not told the other devices are signed out when they
   * are not.
   */
  signOutEverywhere: () => Promise<'signed-out' | 'failed'>;
  /**
   * Mark the session ended where it cannot be renewed (a refresh that failed
   * for a page with no way to try again): the page asks for the account
   * again rather than carrying on with a dead token or as a trial.
   */
  expire: () => void;
}

const AccountSessionContext = createContext<AccountSession | null>(null);

/** What the token says about whom it is for, to tell accounts apart on the page. */
export type TokenIdentity = { subject: string; tenant: string };

/**
 * Read the subject and tenant from an access token, without verifying it.
 *
 * Only for the page to notice that a different account (or a trial) now holds
 * the session, so a project open from the old one is no longer treated as
 * this one's. The server verifies every token and scopes every query itself;
 * nothing here grants or checks access.
 */
export function tokenIdentity(token: string): TokenIdentity | null {
  const part = token.split('.')[1];
  if (part === undefined || part === '') return null;
  try {
    const base64 = part.replace(/-/g, '+').replace(/_/g, '/');
    const padded = base64 + '='.repeat((4 - (base64.length % 4)) % 4);
    const payload: unknown = JSON.parse(atob(padded));
    if (typeof payload !== 'object' || payload === null) return null;
    const { sub, tid } = payload as Record<string, unknown>;
    if (typeof sub !== 'string' || sub === '' || typeof tid !== 'string' || tid === '') return null;
    return { subject: sub, tenant: tid };
  } catch {
    return null;
  }
}

function subjectOf(token: string): string | null {
  return tokenIdentity(token)?.subject ?? null;
}

export function AccountSessionProvider({
  children,
  refreshImpl = refreshSession,
  signOutImpl = signOutOfServer,
  signOutEverywhereImpl = signOutEverywhereOfServer,
  restore = false,
  initial = { status: 'signed-out' },
}: {
  children: ReactNode;
  refreshImpl?: typeof refreshSession;
  signOutImpl?: typeof signOutOfServer;
  signOutEverywhereImpl?: typeof signOutEverywhereOfServer;
  /**
   * Restore the account from the session cookie before rendering anything.
   * The root layout sets it; a page rendered on its own (in a test) does not,
   * and starts from `initial`.
   */
  restore?: boolean;
  /** For tests: the state to start in, when not restoring. */
  initial?: AccountState;
}) {
  const [state, setState] = useState<AccountState>(restore ? { status: 'restoring' } : initial);
  const [restoreTimedOut, setRestoreTimedOut] = useState(false);
  // Read by `renew`, which outlives the render it was made in.
  const current = useRef(state);
  current.current = state;
  // Several requests can 401 at once. One exchange runs per tab and the
  // others wait for its answer; across tabs the server allows the overlap.
  const renewing = useRef<Promise<RenewOutcome> | null>(null);
  // Which exchange is current: a sign-in or sign-out during one supersedes it.
  const ticket = useRef(0);

  const settle = useCallback((next: AccountState) => {
    current.current = next;
    setState(next);
  }, []);

  useEffect(() => {
    if (!restore) return;
    const mine = ticket.current;
    void refreshImpl().then((outcome) => {
      // Signed in or out by hand meanwhile: that stands.
      if (ticket.current !== mine || current.current.status !== 'restoring') return;
      if (outcome.kind === 'refreshed') {
        settle({ status: 'signed-in', tokens: { accessToken: outcome.accessToken } });
        return;
      }
      // No answer in time: this browser may well be signed in, so the page
      // asks for the account rather than starting a trial over it.
      if (outcome.kind === 'timed-out') {
        console.warn('The sign-in could not be restored in time.');
        setRestoreTimedOut(true);
        settle({ status: 'expired' });
        return;
      }
      // Offline or refused: there is no telling whether this browser had a
      // session, so it is treated as having none, and says why in the log.
      if (outcome.kind === 'failed') console.warn('The sign-in could not be restored.');
      settle({ status: 'signed-out' });
    });
  }, [restore, refreshImpl, settle]);

  const signedIn = useCallback(
    (tokens: AccountTokens) => {
      renewing.current = null;
      ticket.current += 1;
      setRestoreTimedOut(false);
      settle({ status: 'signed-in', tokens });
    },
    [settle],
  );

  const signOut = useCallback(() => {
    // Ended on the server, which also clears the cookie. The access token in
    // hand names the session; without one the cookie does. The page is signed
    // out whatever the server answers: a failure (offline) is logged, never a
    // reason to stay signed in.
    const held = current.current;
    // Signed out already (a trial, say): there is no sign-in to end.
    if (held.status !== 'signed-out') {
      void signOutImpl({
        accessToken: held.status === 'signed-in' ? held.tokens.accessToken : null,
      }).then((outcome) => {
        if (outcome.kind === 'failed')
          console.warn('The session could not be ended on the server.');
      });
    }
    setRestoreTimedOut(false);
    renewing.current = null;
    ticket.current += 1;
    settle({ status: 'signed-out' });
  }, [signOutImpl, settle]);

  const signOutEverywhere = useCallback(async (): Promise<'signed-out' | 'failed'> => {
    const held = current.current;
    if (held.status !== 'signed-in') return 'failed';
    const outcome = await signOutEverywhereImpl({ accessToken: held.tokens.accessToken });
    if (outcome.kind === 'failed') return 'failed';
    renewing.current = null;
    ticket.current += 1;
    settle({ status: 'signed-out' });
    return 'signed-out';
  }, [signOutEverywhereImpl, settle]);

  const expire = useCallback(() => {
    if (current.current.status !== 'signed-in') return;
    renewing.current = null;
    ticket.current += 1;
    settle({ status: 'expired' });
  }, [settle]);

  const renew = useCallback((): Promise<RenewOutcome> => {
    if (renewing.current !== null) return renewing.current;
    const held = current.current;
    if (held.status !== 'signed-in') return Promise.resolve('signed-out');
    ticket.current += 1;
    const mine = ticket.current;
    const attempt = (async (): Promise<RenewOutcome> => {
      const outcome = await refreshImpl();
      // Signed out, or in again as someone, while the exchange ran: theirs stands.
      const kind = outcome.kind === 'timed-out' ? 'failed' : outcome.kind;
      if (ticket.current !== mine) return kind;
      renewing.current = null;
      if (outcome.kind === 'refreshed') {
        // The cookie is the browser's, not the tab's: another tab may have
        // signed in as someone else since. This page never becomes that
        // account behind the engineer's back; it asks for its own again.
        const before = subjectOf(held.tokens.accessToken);
        const after = subjectOf(outcome.accessToken);
        if (before !== null && after !== null && before !== after) {
          settle({ status: 'expired' });
          return 'expired';
        }
        settle({ status: 'signed-in', tokens: { accessToken: outcome.accessToken } });
      } else if (outcome.kind === 'expired') {
        settle({ status: 'expired' });
      }
      // `failed` (offline, or timed out): the token is kept, and the next 401
      // tries again.
      return kind;
    })();
    renewing.current = attempt;
    return attempt;
  }, [refreshImpl, settle]);

  const value = useMemo(
    () => ({ state, restoreTimedOut, signedIn, renew, signOut, signOutEverywhere, expire }),
    [state, restoreTimedOut, signedIn, renew, signOut, signOutEverywhere, expire],
  );
  return (
    <AccountSessionContext.Provider value={value}>
      {state.status === 'restoring' ? (
        // Nothing renders until the session is known, so no page starts a
        // trial for a visitor who is in fact signed in.
        <div aria-busy="true" data-testid="account-restoring" className="min-h-dvh" />
      ) : (
        children
      )}
    </AccountSessionContext.Provider>
  );
}

/** The shared account session, or null outside an `AccountSessionProvider`. */
export function useAccountSession(): AccountSession | null {
  return useContext(AccountSessionContext);
}
