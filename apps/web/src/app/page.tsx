'use client';

import Link from 'next/link';
import { useTranslations } from 'next-intl';
import { useCallback, useEffect, useState } from 'react';

import { Chat } from '@/components/chat';
import { LangSwitcher } from '@/components/lang-switcher';
import { ThemeToggle } from '@/components/theme-toggle';
import { readTrial, startTrial, storeTrial, type TrialSession } from '@/lib/trial';

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
 */

type Phase =
  | { kind: 'starting' }
  | { kind: 'ready'; token: string; trial: TrialSession; questionsRemaining: number }
  | { kind: 'unavailable' }
  | { kind: 'failed' };

export default function HomePage() {
  const t = useTranslations('app');
  const tl = useTranslations('landing');
  const [phase, setPhase] = useState<Phase>({ kind: 'starting' });

  const begin = useCallback(async () => {
    setPhase({ kind: 'starting' });

    // A trial already in this browser is reused rather than replaced: starting
    // a second one would strand the first conversation under a tenant the
    // visitor can no longer reach, and burn a fresh quota for no reason.
    const existing = readTrial();

    const outcome = await startTrial();
    if (outcome.kind !== 'started') {
      setPhase({ kind: outcome.kind === 'unavailable' ? 'unavailable' : 'failed' });
      return;
    }

    // `startTrial` returns the pair the claim needs; the token travels
    // alongside it and is not persisted — a reload starts a fresh token
    // against the same trial rather than leaving a credential in storage.
    const payload = outcome as unknown as {
      trial: TrialSession;
      accessToken?: string;
      questionsRemaining?: number;
    };

    const token = payload.accessToken ?? '';
    if (!token) {
      setPhase({ kind: 'failed' });
      return;
    }

    const trial = existing ?? outcome.trial;
    storeTrial(trial);
    setPhase({
      kind: 'ready',
      token,
      trial,
      questionsRemaining: payload.questionsRemaining ?? 0,
    });
  }, []);

  useEffect(() => {
    void begin();
  }, [begin]);

  return (
    <main className="min-h-screen bg-bg p-6 text-text">
      <div className="mb-6 flex flex-wrap items-center justify-between gap-4">
        <h1 className="text-2xl">{t('name')}</h1>
        <div className="flex flex-wrap items-center gap-4">
          <LangSwitcher />
          <ThemeToggle />
        </div>
      </div>

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
              className="mt-2 rounded-md bg-accent px-3 py-1.5 text-sm font-semibold text-accent-contrast"
            >
              {tl('retry')}
            </button>
          </div>
        )}

        {phase.kind === 'ready' && (
          <Chat
            token={phase.token}
            trial={phase.trial}
            questionsRemaining={phase.questionsRemaining}
          />
        )}
      </div>

      <p>
        <Link className="text-accent hover:text-accent-hover" href="/tokens">
          <span>Design tokens</span>
        </Link>
      </p>
    </main>
  );
}
