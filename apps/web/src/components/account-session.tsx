'use client';

import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react';

import { refreshTokens, type RefreshOutcome } from '@/lib/trial';

/**
 * The signed-in account, shared by every page under the app shell.
 *
 * Before this, each page that signed in held its own tokens, so signing in on
 * one page meant nothing on another and `/design` always ran on a trial. The
 * tokens are still held in memory only, as `lib/auth.ts` explains: this is
 * React state in the root layout, so it survives moving between pages and is
 * gone on a reload or when the tab closes. Nothing here touches storage.
 *
 * `expired` is distinct from `signed-out`: a page that was working for an
 * account must ask for that account again, not carry on as someone else (a
 * trial) and save the engineer's work where their account cannot see it.
 */
export type AccountTokens = { accessToken: string; refreshToken: string };

export type AccountState =
  { status: 'signed-out' } | { status: 'signed-in'; tokens: AccountTokens } | { status: 'expired' };

export type RenewOutcome = RefreshOutcome['kind'] | 'signed-out';

export interface AccountSession {
  state: AccountState;
  signedIn: (tokens: AccountTokens) => void;
  /** Exchange the refresh token after a 401; `expired` leaves the account signed out of. */
  renew: () => Promise<RenewOutcome>;
  signOut: () => void;
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

export function AccountSessionProvider({
  children,
  refreshImpl = refreshTokens,
  initial = { status: 'signed-out' },
}: {
  children: ReactNode;
  refreshImpl?: typeof refreshTokens;
  /** For tests: the state to start in. */
  initial?: AccountState;
}) {
  const [state, setState] = useState<AccountState>(initial);
  // Read by `renew`, which outlives the render it was made in.
  const current = useRef(state);
  current.current = state;
  // Several requests can 401 at once. A refresh token is single use, so only
  // one exchange may run; the others wait for its answer.
  const renewing = useRef<Promise<RenewOutcome> | null>(null);
  // Which exchange is current: a sign-in or sign-out during one supersedes it.
  const ticket = useRef(0);

  const signedIn = useCallback((tokens: AccountTokens) => {
    renewing.current = null;
    ticket.current += 1;
    current.current = { status: 'signed-in', tokens };
    setState(current.current);
  }, []);

  const signOut = useCallback(() => {
    renewing.current = null;
    ticket.current += 1;
    current.current = { status: 'signed-out' };
    setState(current.current);
  }, []);

  const renew = useCallback((): Promise<RenewOutcome> => {
    if (renewing.current !== null) return renewing.current;
    const held = current.current;
    if (held.status !== 'signed-in') return Promise.resolve('signed-out');
    ticket.current += 1;
    const mine = ticket.current;
    const attempt = (async (): Promise<RenewOutcome> => {
      const outcome = await refreshImpl({ refreshToken: held.tokens.refreshToken });
      // Signed out, or in again as someone, while the exchange ran: theirs stands.
      if (ticket.current !== mine) return outcome.kind;
      renewing.current = null;
      if (outcome.kind === 'refreshed') {
        current.current = {
          status: 'signed-in',
          tokens: { accessToken: outcome.accessToken, refreshToken: outcome.refreshToken },
        };
        setState(current.current);
      } else if (outcome.kind === 'expired') {
        current.current = { status: 'expired' };
        setState(current.current);
      }
      // `failed` (offline): the tokens are kept, and the next 401 tries again.
      return outcome.kind;
    })();
    renewing.current = attempt;
    return attempt;
  }, [refreshImpl]);

  const value = useMemo(
    () => ({ state, signedIn, renew, signOut }),
    [state, signedIn, renew, signOut],
  );
  return <AccountSessionContext.Provider value={value}>{children}</AccountSessionContext.Provider>;
}

/** The shared account session, or null outside an `AccountSessionProvider`. */
export function useAccountSession(): AccountSession | null {
  return useContext(AccountSessionContext);
}
