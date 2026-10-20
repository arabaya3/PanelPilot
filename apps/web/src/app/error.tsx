'use client';

import Link from 'next/link';
import { useTranslations } from 'next-intl';
import { useEffect } from 'react';

import { AppShell } from '@/components/app-shell';
import { AlertTriangleIcon } from '@/components/icons';
import { EmptyState } from '@/components/ui';

/**
 * A route segment that threw while rendering. The shell stays, so the
 * engineer can retry this screen or move to another tool without a reload.
 *
 * The error's message is not shown: it is written for developers and can
 * carry internal detail. It is logged for whoever is looking at the console.
 */
export default function RouteError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  const t = useTranslations('shell');

  useEffect(() => {
    console.error(error);
  }, [error]);

  return (
    <AppShell>
      <div role="alert">
        <EmptyState
          icon={<AlertTriangleIcon width="24" height="24" aria-hidden="true" />}
          title={
            <span role="heading" aria-level={1}>
              {t('errorTitle')}
            </span>
          }
          action={
            <div className="flex flex-wrap justify-center gap-2">
              <button type="button" className="btn btn-primary" onClick={reset}>
                {t('retry')}
              </button>
              <Link href="/" className="btn btn-secondary">
                {t('home')}
              </Link>
            </div>
          }
        >
          {t('errorBody')}
        </EmptyState>
      </div>
    </AppShell>
  );
}
