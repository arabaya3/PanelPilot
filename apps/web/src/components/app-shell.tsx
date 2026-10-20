'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { useTranslations } from 'next-intl';
import { useEffect, useState, type ComponentType, type ReactNode, type SVGProps } from 'react';

import {
  BoltIcon,
  CalculatorIcon,
  CpuIcon,
  DiagnoseIcon,
  FileTextIcon,
  FolderIcon,
  MenuIcon,
  ShieldCheckIcon,
  TagIcon,
  UsersIcon,
} from '@/components/icons';
import { LangSwitcher } from '@/components/lang-switcher';
import { ThemeToggle } from '@/components/theme-toggle';
import { Dialog } from '@/components/ui';

type NavKey = 'design' | 'diagnose' | 'calc' | 'plc' | 'search' | 'review' | 'team' | 'pricing';

interface NavItem {
  href: string;
  key: NavKey;
  Icon: ComponentType<SVGProps<SVGSVGElement>>;
}

/** The tools, in the order an engineer reaches for them. */
const PRIMARY: readonly NavItem[] = [
  { href: '/design', key: 'design', Icon: FolderIcon },
  { href: '/', key: 'diagnose', Icon: DiagnoseIcon },
  { href: '/calc', key: 'calc', Icon: CalculatorIcon },
  { href: '/plc', key: 'plc', Icon: CpuIcon },
  { href: '/search', key: 'search', Icon: FileTextIcon },
  { href: '/review', key: 'review', Icon: ShieldCheckIcon },
];

/** The account, at the foot of the rail. */
const SECONDARY: readonly NavItem[] = [
  { href: '/team', key: 'team', Icon: UsersIcon },
  { href: '/pricing', key: 'pricing', Icon: TagIcon },
];

const DRAWER_ID = 'app-shell-drawer';

type Layout = 'rail' | 'drawer';

/**
 * The frame every screen sits in: a top bar (brand, context, language, theme,
 * page actions) and a rail of tools on the inline-start edge.
 *
 * The rail shows an icon above a visible label: icons alone are a guessing
 * game, and an engineer with a glove on should not have to hover to learn
 * what a button does. The current tool is marked for sight (gold) and for a
 * screen reader (`aria-current`). Below `md` the same links open in a drawer
 * behind a menu button. Logical properties put the rail on the right in
 * Arabic and Hebrew without any direction-specific code.
 */
export function AppShell({
  children,
  actions,
  context,
  wide = false,
}: {
  children: ReactNode;
  /** Page-specific controls for the end of the top bar, such as Sign in. */
  actions?: ReactNode;
  /** Where the user is, such as the open project, shown beside the brand. */
  context?: ReactNode;
  /** Drop the max-width, for workspaces that use the whole screen. */
  wide?: boolean;
}) {
  const t = useTranslations('nav');
  const tApp = useTranslations('app');
  const tShell = useTranslations('shell');
  const tUi = useTranslations('ui');
  // `null` outside the app router (a unit test); no link is current then.
  const pathname = usePathname() as string | null;
  const [menuOpen, setMenuOpen] = useState(false);

  // A followed link changes the route; the drawer has done its job.
  useEffect(() => {
    setMenuOpen(false);
  }, [pathname]);

  function renderLinks(items: readonly NavItem[], layout: Layout) {
    return (
      <ul className="flex flex-col gap-1">
        {items.map(({ href, key, Icon }) => {
          const current = pathname === href;
          const tone = current
            ? 'bg-accent-subtle font-semibold text-accent'
            : 'font-medium text-text-muted hover:bg-surface-raised hover:text-text';
          return (
            <li key={href}>
              <Link
                href={href}
                aria-current={current ? 'page' : undefined}
                className={
                  layout === 'rail'
                    ? `relative flex flex-col items-center gap-1 rounded-md px-1 py-2 text-center text-xs leading-tight transition-colors duration-150 ${tone}`
                    : `flex items-center gap-3 rounded-md px-3 py-2 text-sm transition-colors duration-150 ${tone}`
                }
                style={{ minHeight: '2.75rem' }}
              >
                {current && layout === 'rail' && (
                  // A gold bar on the rail's inner edge marks the current tool.
                  <span
                    aria-hidden="true"
                    className="absolute inset-y-2 start-0 rounded-full bg-accent"
                    style={{ width: '3px' }}
                  />
                )}
                <Icon width="20" height="20" aria-hidden="true" className="shrink-0" />
                <span className="max-w-full break-words">{t(key)}</span>
              </Link>
            </li>
          );
        })}
      </ul>
    );
  }

  function renderNav(layout: Layout) {
    return (
      <nav
        aria-label={t('label')}
        className={`flex flex-1 flex-col justify-between gap-4 ${layout === 'rail' ? 'px-2 py-3' : ''}`}
      >
        {renderLinks(PRIMARY, layout)}
        <div className="border-t border-border-subtle pt-3">{renderLinks(SECONDARY, layout)}</div>
      </nav>
    );
  }

  return (
    <div className="flex min-h-screen flex-col bg-bg text-text">
      <header className="sticky top-0 z-20 border-b border-border-subtle bg-chrome">
        <div className="flex w-full items-center gap-2 px-3 py-2 md:gap-3 md:px-4">
          <button
            type="button"
            className="btn-icon md:hidden"
            aria-label={tShell('openMenu')}
            aria-expanded={menuOpen}
            aria-controls={DRAWER_ID}
            onClick={() => {
              setMenuOpen(true);
            }}
          >
            <MenuIcon width="20" height="20" aria-hidden="true" />
          </button>
          <Link
            href="/"
            className="flex shrink-0 items-center gap-2 rounded-md py-1 text-base font-semibold text-text md:text-lg"
          >
            <span className="flex h-6 w-6 items-center justify-center rounded-sm border border-accent text-accent">
              <BoltIcon width="16" height="16" aria-hidden="true" />
            </span>
            {tApp('name')}
          </Link>
          {context && (
            <div className="hidden min-w-0 items-center gap-2 border-s border-border-subtle ps-3 text-sm text-text-muted sm:flex">
              {context}
            </div>
          )}
          <div className="ms-auto flex shrink-0 items-center gap-2">
            <LangSwitcher />
            <ThemeToggle />
            {actions}
          </div>
        </div>
        {/* On a phone the context gets its own line rather than pushing the
            brand and controls off the edge. */}
        {context && (
          <div className="flex min-w-0 items-center gap-2 border-t border-border-subtle px-3 py-2 text-sm text-text-muted sm:hidden">
            {context}
          </div>
        )}
      </header>

      <div className="flex flex-1">
        <aside
          className="hidden shrink-0 flex-col border-e border-border-subtle bg-chrome md:flex"
          style={{ width: '5.5rem' }}
        >
          {renderNav('rail')}
        </aside>

        <main
          className={`mx-auto flex w-full min-w-0 flex-1 flex-col px-4 py-5 md:px-6 md:py-6 ${
            wide ? '' : 'max-w-screen-xl'
          }`}
        >
          {children}
        </main>
      </div>

      <div id={DRAWER_ID}>
        <Dialog
          open={menuOpen}
          onClose={() => {
            setMenuOpen(false);
          }}
          title={tShell('menuTitle')}
          variant="drawer-start"
          closeLabel={tUi('close')}
        >
          {/* Rendered only while open, so the links are never in the
              accessibility tree twice. */}
          {menuOpen && renderNav('drawer')}
        </Dialog>
      </div>
    </div>
  );
}
