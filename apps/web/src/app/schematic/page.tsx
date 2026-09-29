'use client';

import Link from 'next/link';
import { useTranslations } from 'next-intl';
import { useCallback, useEffect, useState } from 'react';

import { LangSwitcher } from '@/components/lang-switcher';
import { SchematicWorkbench } from '@/components/schematic/workbench';
import { ThemeToggle } from '@/components/theme-toggle';
import { readTrial, startTrial, storeTrial } from '@/lib/trial';

/**
 * The single-line schematic page (PD-008).
 *
 * Authenticates the same way the front door does — the anonymous trial — so
 * the drawing works without an account. Drawing a schematic costs no free
 * question: it reads nothing and calls no model.
 */
type Phase = { kind: 'starting' } | { kind: 'ready'; token: string } | { kind: 'failed' };

export default function SchematicPage() {
  const t = useTranslations('schematic');
  const tl = useTranslations('landing');
  const [phase, setPhase] = useState<Phase>({ kind: 'starting' });

  const begin = useCallback(async () => {
    setPhase({ kind: 'starting' });
    const existing = readTrial();
    const outcome = await startTrial();
    if (outcome.kind !== 'started' || outcome.accessToken === '') {
      setPhase({ kind: 'failed' });
      return;
    }
    // Same rule as the front door: a trial already in this browser is kept,
    // so a conversation started there is not stranded by visiting this page.
    storeTrial(existing ?? outcome.trial);
    setPhase({ kind: 'ready', token: outcome.accessToken });
  }, []);

  useEffect(() => {
    void begin();
  }, [begin]);

  return (
    <main className="min-h-screen bg-bg p-6 text-text">
      <div className="mb-6 flex flex-wrap items-center justify-between gap-4 print:hidden">
        <h1 className="text-2xl">{t('heading')}</h1>
        <div className="flex flex-wrap items-center gap-4">
          <Link className="text-accent hover:text-accent-hover" href="/">
            PanelPilot
          </Link>
          <LangSwitcher />
          <ThemeToggle />
        </div>
      </div>

      <p className="mb-6 max-w-3xl text-text-muted print:hidden">{t('intro')}</p>

      {phase.kind === 'starting' && (
        <p className="text-sm text-text-muted" data-testid="schematic-starting">
          {tl('starting')}
        </p>
      )}
      {phase.kind === 'failed' && (
        <div
          role="alert"
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
      {phase.kind === 'ready' && <SchematicWorkbench token={phase.token} />}
    </main>
  );
}
