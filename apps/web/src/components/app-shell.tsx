'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { useTranslations } from 'next-intl';
import type { ReactNode } from 'react';

import { BoltIcon, ChatIcon, CheckCircleIcon, CodeIcon, SearchIcon } from '@/components/icons';
import { LangSwitcher } from '@/components/lang-switcher';
import { ThemeToggle } from '@/components/theme-toggle';

const NAV = [
  { href: '/', key: 'diagnose', Icon: ChatIcon },
  { href: '/search', key: 'search', Icon: SearchIcon },
  { href: '/plc', key: 'plc', Icon: CodeIcon },
  { href: '/review', key: 'review', Icon: CheckCircleIcon },
] as const;

/**
 * The frame every screen sits in: one header, one way around.
 *
 * Each page used to draw its own title row with its own copy of the language
 * and theme controls, and the only way from the chat to the PLC checker was a
 * text link under the conversation. The three tools are now always one tap
 * away, and the current one is marked for sight (the tint) and for a screen
 * reader (`aria-current`).
 *
 * On a phone the navigation drops to its own row under the brand rather than
 * behind a menu button: three short links fit at 360px, and a hidden menu is
 * one more thing to find with a glove on.
 */
export function AppShell({
  children,
  actions,
}: {
  children: ReactNode;
  /** Page-specific controls for the end of the header, such as Sign in. */
  actions?: ReactNode;
}) {
  const t = useTranslations('nav');
  const tApp = useTranslations('app');
  // `null` outside the app router (a unit test); no link is current then.
  const pathname = usePathname() as string | null;

  const links = NAV.map(({ href, key, Icon }) => {
    const current = pathname === href;
    return (
      <Link
        key={href}
        href={href}
        aria-current={current ? 'page' : undefined}
        className={`inline-flex items-center gap-2 whitespace-nowrap rounded-md px-2 py-2 text-sm transition-colors sm:px-3 ${
          current
            ? 'bg-accent-subtle font-semibold text-accent-hover'
            : 'font-medium text-text-muted hover:bg-surface-raised hover:text-text'
        }`}
      >
        {/* Text only on a phone: four links with icons do not fit 360px,
            and the last one scrolled out of sight with nothing to say so. */}
        <Icon width="16" height="16" className="max-sm:hidden" />
        {t(key)}
      </Link>
    );
  });

  return (
    <div className="flex min-h-screen flex-col bg-bg text-text">
      <header className="sticky top-0 z-10 border-b border-border-subtle bg-surface">
        <div className="mx-auto flex w-full flex-wrap items-center gap-x-3 gap-y-2 px-4 py-2 md:flex-nowrap md:px-6 max-w-screen-xl">
          <Link
            href="/"
            className="flex shrink-0 items-center gap-2 rounded-md py-1 text-base font-bold text-text md:text-lg"
          >
            <span className="flex h-6 w-6 items-center justify-center rounded-md bg-accent text-accent-contrast shadow-sm">
              <BoltIcon width="18" height="18" />
            </span>
            {tApp('name')}
          </Link>
          {/* One list, reflowed: its own full-width row under the brand on a
              phone, inline beside it from `md`. Two copies would put every
              link in the accessibility tree twice. */}
          <nav
            aria-label={t('label')}
            className="order-last -mx-1 flex w-full items-center gap-1 overflow-x-auto md:order-none md:mx-0 md:w-auto"
          >
            {links}
          </nav>
          <div className="ms-auto flex items-center gap-2">
            <LangSwitcher />
            <ThemeToggle />
            {actions}
          </div>
        </div>
      </header>
      <main className="mx-auto flex w-full flex-1 flex-col px-4 py-5 md:px-6 md:py-6 max-w-screen-xl">
        {children}
      </main>
    </div>
  );
}
