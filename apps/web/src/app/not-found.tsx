'use client';

import Link from 'next/link';
import { useTranslations } from 'next-intl';

import { AppShell } from '@/components/app-shell';
import { FileTextIcon } from '@/components/icons';
import { EmptyState } from '@/components/ui';

/**
 * An unknown address, inside the shell so every tool is still one tap away.
 *
 * A client component because the strings come from the client-side locale
 * provider; the server cannot know the language stored in the browser.
 */
export default function NotFound() {
  const t = useTranslations('shell');
  return (
    <AppShell>
      <EmptyState
        icon={<FileTextIcon width="24" height="24" aria-hidden="true" />}
        title={
          <span role="heading" aria-level={1}>
            {t('notFoundTitle')}
          </span>
        }
        action={
          <Link href="/" className="btn btn-secondary">
            {t('home')}
          </Link>
        }
      >
        {t('notFoundBody')}
      </EmptyState>
    </AppShell>
  );
}
