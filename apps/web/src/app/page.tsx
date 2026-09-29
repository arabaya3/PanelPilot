'use client';

import Link from 'next/link';
import { useTranslations } from 'next-intl';
import { useCallback, useEffect, useRef, useState } from 'react';

import { Chat } from '@/components/chat';
import { AppShell } from '@/components/app-shell';
import { SignInForm } from '@/components/sign-in-form';
import { UserIcon } from '@/components/icons';
import {
  clearTrial,
  readTrial,
  refreshTokens,
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
 * Tokens live in memory only — state for the access token, a ref for the
 * refresh token — and never in storage. The claim pair has to survive a
 * reload; a bearer credential left on a shared workshop terminal outlives the
 * person who used it.
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
  const t = useTranslations('app');
  const tl = useTranslations('landing');
  const ts = useTranslations('signIn');
  const [phase, setPhase] = useState<Phase>({ kind: 'starting' });
  const [signingIn, setSigningIn] = useState(false);
  // Bumped on sign-in so the chat mounts fresh. Signing in moves to another
  // tenant, where the trial's open conversation does not exist: kept mounted,
  // the next question would name it and be refused. Signup does not bump it,
  // because signup joins the trial's own tenant and the conversation carries.
  const [chatKey, setChatKey] = useState(0);

  // Held in a ref, not state and not storage: nothing renders from it, and it
  // must not outlive the tab.
  const refreshRef = useRef<string | null>(null);

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

  const onSignedUp = useCallback((tokens: { accessToken: string; refreshToken: string }) => {
    refreshRef.current = tokens.refreshToken;
    // The claim has been made; the secret has no further use, and a stored
    // trial would have the next reload try to resume one that no longer
    // exists as a trial.
    clearTrial();
    setPhase({
      kind: 'ready',
      token: tokens.accessToken,
      trial: null,
      questionsRemaining: null,
      // Already open: the account continues in the conversation on screen.
      conversationId: null,
    });
  }, []);

  const onSignedIn = useCallback((tokens: { accessToken: string; refreshToken: string }) => {
    refreshRef.current = tokens.refreshToken;
    setSigningIn(false);
    setChatKey((key) => key + 1);
    // The stored trial is left alone: it was not claimed, and it is still
    // this browser's to return to after the account's in-memory tokens go.
    setPhase({
      kind: 'ready',
      token: tokens.accessToken,
      trial: null,
      questionsRemaining: null,
      conversationId: null,
    });
  }, []);

  const onUnauthorized = useCallback(async () => {
    // An account renews with its refresh token. Falling through to `begin`
    // when that fails is the best available: there is no sign-in form here,
    // so a lapsed account can only continue as a trial.
    const refreshToken = refreshRef.current;
    if (refreshToken !== null) {
      const refreshed = await refreshTokens({ refreshToken });
      if (refreshed.kind === 'refreshed') {
        refreshRef.current = refreshed.refreshToken;
        setPhase((current) =>
          current.kind === 'ready' ? { ...current, token: refreshed.accessToken } : current,
        );
        return;
      }
      refreshRef.current = null;
    }
    await begin({ keepSurface: true });
  }, [begin]);

  useEffect(() => {
    void begin();
  }, [begin]);

  const signInButton =
    // Offered wherever the visitor is not already in an account.
    !(phase.kind === 'ready' && phase.trial === null) && !signingIn ? (
      <button
        type="button"
        onClick={() => {
          setSigningIn(true);
        }}
        className="btn btn-sm btn-secondary"
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
      <section className="mx-auto mb-4 flex max-w-3xl flex-col items-center gap-3 text-center md:mb-6">
        <span className="chip max-sm:hidden">{tl('eyebrow')}</span>
        <h1 className="text-xl font-bold tracking-tight sm:text-2xl md:text-3xl">
          {tl('headline')}
        </h1>
        <p className="max-w-2xl text-base text-text-muted max-sm:hidden md:text-lg">
          {tl('subhead')}
        </p>
      </section>

      {signingIn && (
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
        {phase.kind === 'starting' && (
          <div className="card flex items-center justify-center gap-3 p-7">
            <span
              aria-hidden="true"
              className="h-4 w-4 animate-spin rounded-full border-2 border-border-subtle border-t-accent"
            />
            <p data-testid="landing-starting" className="text-sm text-text-muted">
              {tl('starting')}
            </p>
          </div>
        )}

        {phase.kind === 'unavailable' && (
          <p
            role="alert"
            data-testid="landing-unavailable"
            className="rounded-lg border border-severity-warning bg-severity-warning-surface p-4 text-sm text-severity-warning"
          >
            {tl('unavailable')}
          </p>
        )}

        {phase.kind === 'rate-limited' && (
          <div
            role="alert"
            data-testid="landing-rate-limited"
            className="flex flex-col items-start gap-3 rounded-lg border border-severity-warning bg-severity-warning-surface p-4 text-sm text-severity-warning"
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
            className="flex flex-col items-start gap-3 rounded-lg border border-severity-critical bg-severity-critical-surface p-4 text-sm text-severity-critical"
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
          <div className="card h-[calc(100dvh-13rem)] min-h-96 md:h-[calc(100dvh-19rem)] overflow-hidden rounded-xl shadow-lg">
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
