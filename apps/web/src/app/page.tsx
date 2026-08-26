'use client';

import Link from 'next/link';
import { useTranslations } from 'next-intl';

import { DiagnosisSample } from '@/components/diagnosis-sample';
import { LangSwitcher } from '@/components/lang-switcher';
import { ThemeToggle } from '@/components/theme-toggle';

export default function HomePage() {
  const t = useTranslations('app');

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

      <div className="mb-6 max-w-2xl">
        <DiagnosisSample />
      </div>

      <p>
        <Link className="text-accent hover:text-accent-hover" href="/tokens">
          <span>Design tokens</span>
        </Link>
      </p>
    </main>
  );
}
