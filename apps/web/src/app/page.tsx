'use client';

import Link from 'next/link';
import { useTranslations } from 'next-intl';
import { useCallback, useEffect, useRef, useState } from 'react';

import { Chat } from '@/components/chat';
import {
  AccountSessionProvider,
  useAccountSession,
  type AccountSession,
} from '@/components/account-session';
import { AppShell } from '@/components/app-shell';
import { SignInForm } from '@/components/sign-in-form';
import { UserIcon } from '@/components/icons';
import {
  clearTrial,
  readTrial,
  resumeTrial,
  startTrial,
  storeTrial,
  type ActiveTrial,
  type TrialSession,
} from '@/lib/trial';

/**
 * The front door.
 *
 * FE-008's deliberate contrast with an invite-only funnel: the chat input is
 * the first thing on the page, with no access form in front of it. A visitor
 * types a question and gets an answer; signup happens later, when the free
 * questions run out, and carries the conversation into the new account.
 *
 * The trial is started on mount rather than on first keystroke. Starting it
 * lazily would put a round trip between pressing enter and anything happening,
 * which reads as the product being slow at exactly the moment it is being
 * judged.
 *
 * A trial that cannot start says so. `startTrial` distinguishes `unavailable`
 * (no such endpoint) from `failed` (it broke), and both surface as a message
 * rather than an input that silently does nothing — which is what an engineer
 * standing at a panel would otherwise get.
 *
 * The access token lives in memory only, never in storage; the refresh token
 * is the HttpOnly session cookie, which no script here can read (see
 * `lib/auth.ts`). The claim pair has to survive a reload; a bearer credential
 * left in storage on a shared workshop terminal outlives the person who used it.
 *
 * Signing in from a trial asks first. An existing account does not take the
 * trial's conversation: it stays in the trial's tenant, reachable again by
 * continuing as the trial in this browser, and never shown to the account.
 */

type Phase =
  | { kind: 'starting' }
  | {
      kind: 'ready';
      token: string;
      /** `null` once signed up: the trial has been claimed into the account. */
      trial: TrialSession | null;
      /** `null` when unknown, which keeps the limit modal away. */
      questionsRemaining: number | null;
      /** The trial's own conversation, opened on arrival; see `Chat`. */
      conversationId: string | null;
    }
  | { kind: 'unavailable' }
  | { kind: 'rate-limited' }
  | { kind: 'failed' };

function readyWith(active: ActiveTrial): Phase {
  return {
    kind: 'ready',
    token: active.accessToken,
    trial: active.trial,
    questionsRemaining: active.questionsRemaining,
    conversationId: active.conversationId,
  };
}

export default function HomePage() {
  const shared = useAccountSession();
  // The root layout shares one; a page rendered on its own (in a test, say)
  // keeps a session of its own, so it behaves the same either way.
  if (shared === null) {
    return (
      <AccountSessionProvider>
        <HomePage />
      </AccountSessionProvider>
    );
  }
  return <Home account={shared} />;
}

function accountPhase(token: string): Phase {
  return { kind: 'ready', token, trial: null, questionsRemaining: null, conversationId: null };
}

function Home({ account }: { account: AccountSession }) {
  const t = useTranslations('app');
  const tl = useTranslations('landing');
  const ts = useTranslations('signIn');
  const { state: accountState, signedIn: shareAccount, renew, expire, signOut } = account;
  const accountToken = accountState.status === 'signed-in' ? accountState.tokens.accessToken : null;
  // Shared with the other pages (notably /design): an account signed in on
  // any of them is the one this page works under on arrival, and an account
  // signed in here is theirs too. Only a visitor in no account starts a trial.
  const [phase, setPhase] = useState<Phase>(() =>
    accountToken !== null ? accountPhase(accountToken) : { kind: 'starting' },
  );
  const startsAsTrial = useRef(accountState.status === 'signed-out');
  const [signingIn, setSigningIn] = useState(false);
  // A trial asked to confirm before signing in: an existing account does not
  // take the trial's conversation, which stays with this browser's trial.
  const [confirmingTrialSignIn, setConfirmingTrialSignIn] = useState(false);
  // The account's session ended and could not be renewed: it is asked for
  // again, never replaced by a trial behind the engineer's back.
  const expired = accountState.status === 'expired';
  const inAccount = phase.kind === 'ready' && phase.trial === null;
  // Bumped on sign-in so the chat mounts fresh. Signing in moves to another
  // tenant, where the trial's open conversation does not exist: kept mounted,
  // the next question would name it and be refused. Signup does not bump it,
  // because signup joins the trial's own tenant and the conversation carries.
  const [chatKey, setChatKey] = useState(0);

  /**
   * Get a working token: resume this browser's trial, or start one.
   *
   * `keepSurface` is for re-authenticating under a mounted chat. Showing
   * `starting` would unmount it and throw away the transcript the engineer is
   * looking at, all to replace a token they never see.
   */
  const begin = useCallback(async ({ keepSurface = false } = {}) => {
    if (!keepSurface) setPhase({ kind: 'starting' });

    // A trial already in this browser is resumed rather than replaced:
    // starting a second would strand the first conversation under a tenant
    // the visitor can no longer reach, and burn a fresh quota for no reason.
    const existing = readTrial();
    if (existing) {
      const resumed = await resumeTrial(existing);
      if (resumed.kind === 'resumed') {
        storeTrial(resumed.trial);
        setPhase(readyWith(resumed));
        return;
      }
      // A network or server failure keeps the stored trial, so that a retry
      // once the API is back still reaches the same conversation.
      if (resumed.kind === 'failed') {
        setPhase({ kind: 'failed' });
        return;
      }
      if (resumed.kind === 'rate-limited') {
        setPhase({ kind: 'rate-limited' });
        return;
      }
      // Gone: expired, claimed, or unknown. Nothing can resume it, so it is
      // forgotten and a new trial takes its place.
      clearTrial();
    }

    const outcome = await startTrial();
    if (outcome.kind !== 'started') {
      setPhase({ kind: outcome.kind });
      return;
    }

    // The trial stored and the token used both come from this one response.
    // Mixing a stored trial with a new trial's token is how the claim ended up
    // naming a tenant other than the one the questions were asked in.
    storeTrial(outcome.trial);
    setPhase(readyWith(outcome));
  }, []);

  const onSignedUp = useCallback(
    (tokens: { accessToken: string }) => {
      shareAccount(tokens);
      // The claim has been made; the secret has no further use, and a stored
      // trial would have the next reload try to resume one that no longer
      // exists as a trial.
      clearTrial();
      // Already open: the account continues in the conversation on screen.
      setPhase(accountPhase(tokens.accessToken));
    },
    [shareAccount],
  );

  const onSignedIn = useCallback(
    (tokens: { accessToken: string }) => {
      shareAccount(tokens);
      setSigningIn(false);
      setChatKey((key) => key + 1);
      // The stored trial is left alone: it was not claimed, and it is still
      // this browser's to return to after the account's in-memory tokens go.
      setPhase(accountPhase(tokens.accessToken));
    },
    [shareAccount],
  );

  const onUnauthorized = useCallback(async () => {
    if (!inAccount) {
      await begin({ keepSurface: true });
      return;
    }
    // The shared session renews (one exchange, whichever page asks), and the
    // new token reaches the chat through `accountToken` below. When it cannot,
    // the shared session is marked ended, so no page carries on with the dead
    // token, and this one asks for the account again instead of a trial.
    const outcome = await renew();
    if (outcome !== 'refreshed') expire();
  }, [inAccount, begin, renew, expire]);

  // The account's token, as the shared session renews it.
  useEffect(() => {
    if (accountToken === null) return;
    setPhase((current) =>
      current.kind === 'ready' && current.trial === null && current.token !== accountToken
        ? { ...current, token: accountToken }
        : current,
    );
  }, [accountToken]);

  useEffect(() => {
    // Only a visitor in no account starts (or resumes) a trial on arrival.
    if (startsAsTrial.current) void begin();
  }, [begin]);

  /** Leave the ended account explicitly, for a trial. */
  function continueAsTrial() {
    signOut();
    void begin();
  }

  const signInButton =
    // Offered wherever the visitor is not already in an account.
    !inAccount && !signingIn && !confirmingTrialSignIn && !expired ? (
      <button
        type="button"
        onClick={() => {
          if (phase.kind === 'ready' && phase.trial !== null) setConfirmingTrialSignIn(true);
          else setSigningIn(true);
        }}
        className="btn btn-sm btn-secondary max-sm:px-2"
      >
        <UserIcon width="16" height="16" />
        {/* Icon-only on a phone, where the header is short of room; the
            name stays for a screen reader either way. */}
        <span className="max-sm:sr-only">{ts('open')}</span>
      </button>
    ) : null;

  return (
    <AppShell actions={signInButton}>
      {/* Pared down on a phone to the headline alone, so the question box is
          on the first screen rather than under the pitch. */}
      <section className="mx-auto mb-4 flex w-full max-w-screen-lg flex-col gap-2 md:mb-5">
        <span className="eyebrow max-sm:hidden">{tl('eyebrow')}</span>
        <h1 className="text-xl font-semibold tracking-tight md:text-2xl">{tl('headline')}</h1>
        <p className="max-w-2xl text-sm text-text-muted max-sm:hidden md:text-base">
          {tl('subhead')}
        </p>
      </section>

      {expired && (
        <div
          className="mx-auto mb-6 flex w-full max-w-sm flex-col gap-3"
          data-testid="landing-expired"
        >
          <p
            role="alert"
            className="rounded-md border border-severity-warning bg-severity-warning-surface p-4 text-sm text-severity-warning"
          >
            {account.restoreTimedOut ? tl('restoreTimedOut') : tl('expired')}
          </p>
          <SignInForm onSignedIn={onSignedIn} />
          <button type="button" onClick={continueAsTrial} className="btn btn-sm btn-secondary">
            {tl('continueAsTrial')}
          </button>
        </div>
      )}

      {confirmingTrialSignIn && !signingIn && !expired && (
        <div
          role="alertdialog"
          aria-labelledby="landing-trial-sign-in-title"
          data-testid="landing-trial-sign-in"
          className="mx-auto mb-6 flex w-full max-w-sm flex-col gap-3 rounded-md border border-severity-warning bg-severity-warning-surface p-4 text-sm"
        >
          <p id="landing-trial-sign-in-title" className="font-semibold">
            {tl('trialSignInTitle')}
          </p>
          <p>{tl('trialSignInBody')}</p>
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              className="btn btn-sm btn-primary"
              onClick={() => {
                setConfirmingTrialSignIn(false);
                setSigningIn(true);
              }}
            >
              {tl('trialSignInContinue')}
            </button>
            <button
              type="button"
              className="btn btn-sm btn-secondary"
              onClick={() => {
                setConfirmingTrialSignIn(false);
              }}
            >
              {tl('trialSignInCancel')}
            </button>
          </div>
        </div>
      )}

      {signingIn && !expired && (
        <div className="mx-auto mb-6 w-full max-w-sm">
          <SignInForm
            onSignedIn={onSignedIn}
            onCancel={() => {
              setSigningIn(false);
            }}
          />
        </div>
      )}

      <div className="mx-auto w-full max-w-screen-lg">
        {phase.kind === 'starting' && !expired && (
          <div className="panel flex items-center justify-center gap-3 p-7">
            <span aria-hidden="true" className="spinner" />
            <p data-testid="landing-starting" className="text-sm text-text-muted">
              {tl('starting')}
            </p>
          </div>
        )}

        {phase.kind === 'unavailable' && (
          <p
            role="alert"
            data-testid="landing-unavailable"
            className="rounded-md border border-severity-warning bg-severity-warning-surface p-4 text-sm text-severity-warning"
          >
            {tl('unavailable')}
          </p>
        )}

        {phase.kind === 'rate-limited' && (
          <div
            role="alert"
            data-testid="landing-rate-limited"
            className="flex flex-col items-start gap-3 rounded-md border border-severity-warning bg-severity-warning-surface p-4 text-sm text-severity-warning"
          >
            <p>{tl('rateLimited')}</p>
            <button type="button" onClick={() => void begin()} className="btn btn-sm btn-primary">
              {tl('retry')}
            </button>
          </div>
        )}

        {phase.kind === 'failed' && (
          <div
            role="alert"
            data-testid="landing-failed"
            className="flex flex-col items-start gap-3 rounded-md border border-severity-critical bg-severity-critical-surface p-4 text-sm text-severity-critical"
          >
            <p>{tl('failed')}</p>
            <button type="button" onClick={() => void begin()} className="btn btn-sm btn-primary">
              {tl('retry')}
            </button>
          </div>
        )}

        {phase.kind === 'ready' && (
          // A fixed height, so the transcript scrolls inside the card with the
          // question box pinned under it, rather than the page growing a
          // screen per answer.
          <div className="panel h-[calc(100dvh-13rem)] min-h-96 md:h-[calc(100dvh-19rem)] overflow-hidden">
            <Chat
              key={chatKey}
              token={phase.token}
              trial={phase.trial}
              questionsRemaining={phase.questionsRemaining}
              conversationId={phase.conversationId}
              onSignedUp={onSignedUp}
              onUnauthorized={() => void onUnauthorized()}
            />
          </div>
        )}
      </div>

      <footer className="mt-auto flex flex-wrap items-center justify-center gap-x-4 gap-y-1 pt-7 text-xs text-text-muted">
        <span>{t('tagline')}</span>
        <Link className="link" href="/tokens">
          Design tokens
        </Link>
      </footer>
    </AppShell>
  );
}
