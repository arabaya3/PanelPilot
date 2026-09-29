'use client';

import { useTranslations } from 'next-intl';

import { MoonIcon, SunIcon } from '@/components/icons';
import { useTheme } from '@/components/theme-provider';

/**
 * Switches the application between light and dark.
 *
 * Labelled with what it will do rather than what is currently active — "Dark
 * mode" on a button that is already dark reads as a status, and people click
 * it expecting nothing to happen.
 */
export function ThemeToggle() {
  const { theme, toggleTheme } = useTheme();
  const t = useTranslations('theme');
  const next = theme === 'dark' ? 'light' : 'dark';

  return (
    <button
      type="button"
      onClick={toggleTheme}
      // The pressed state is what a screen reader announces; the visible label
      // says where the button leads.
      aria-pressed={theme === 'dark'}
      title={t('switchTo', { theme: t(next) })}
      className="btn-icon"
    >
      {/* The icon shows where the button leads, like the label it replaces. */}
      {next === 'dark' ? <MoonIcon /> : <SunIcon />}
      <span className="sr-only">{t('switchTo', { theme: t(next) })}</span>
    </button>
  );
}
