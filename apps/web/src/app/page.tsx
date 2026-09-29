'use client';

import Link from 'next/link';
import { useTranslations } from 'next-intl';
import { useCallback, useEffect, useRef, useState } from 'react';

import { Chat } from '@/components/chat';
import { LangSwitcher } from '@/components/lang-switcher';
import { SignInForm } from '@/components/sign-in-form';
import { ThemeToggle } from '@/components/theme-toggle';
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
      setPhase({ kind: outcome.kind === 'unavailable' ? 'unavailable' : 'failed' });
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

  return (
    <main className="min-h-screen bg-bg p-6 text-text">
      <div className="mb-6 flex flex-wrap items-center justify-between gap-4">
        <h1 className="text-2xl">{t('name')}</h1>
        <div className="flex flex-wrap items-center gap-4">
          {/* Offered wherever the visitor is not already in an account. */}
          {!(phase.kind === 'ready' && phase.trial === null) && !signingIn && (
            <button
              type="button"
              onClick={() => {
                setSigningIn(true);
              }}
              className="rounded-md border border-border px-3 py-2 text-sm text-text"
            >
              {ts('open')}
            </button>
          )}
          <LangSwitcher />
          <ThemeToggle />
        </div>
      </div>

      {signingIn && (
        <div className="mb-6">
          <SignInForm
            onSignedIn={onSignedIn}
            onCancel={() => {
              setSigningIn(false);
            }}
          />
        </div>
      )}

      <p className="mb-6 max-w-2xl text-text-muted">{t('tagline')}</p>

      <div className="mb-6 max-w-3xl">
        {phase.kind === 'starting' && (
          <p data-testid="landing-starting" className="text-sm text-text-muted">
            {tl('starting')}
          </p>
        )}

        {phase.kind === 'unavailable' && (
          <p
            role="alert"
            data-testid="landing-unavailable"
            className="rounded-md border border-severity-warning bg-severity-warning-surface p-3 text-sm text-severity-warning"
          >
            {tl('unavailable')}
          </p>
        )}

        {phase.kind === 'failed' && (
          <div
            role="alert"
            data-testid="landing-failed"
            className="rounded-md border border-severity-critical bg-severity-critical-surface p-3 text-sm text-severity-critical"
          >
            <p>{tl('failed')}</p>
            <button
              type="button"
              onClick={() => void begin()}
              className="mt-2 rounded-md bg-accent px-3 py-2 text-sm font-semibold text-accent-contrast"
            >
              {tl('retry')}
            </button>
          </div>
        )}

        {phase.kind === 'ready' && (
          <Chat
            key={chatKey}
            token={phase.token}
            trial={phase.trial}
            questionsRemaining={phase.questionsRemaining}
            conversationId={phase.conversationId}
            onSignedUp={onSignedUp}
            onUnauthorized={() => void onUnauthorized()}
          />
        )}
      </div>

      <p className="flex flex-wrap gap-4">
        <Link className="text-accent hover:text-accent-hover" href="/plc">
          {tl('plcLink')}
        </Link>
        <Link className="text-accent hover:text-accent-hover" href="/review">
          {tl('reviewLink')}
        </Link>
        <Link className="text-accent hover:text-accent-hover" href="/tokens">
          <span>Design tokens</span>
        </Link>
      </p>
    </main>
  );
}
