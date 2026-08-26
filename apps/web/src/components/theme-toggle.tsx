'use client';

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
  const next = theme === 'dark' ? 'light' : 'dark';

  return (
    <button
      type="button"
      onClick={toggleTheme}
      // The pressed state is what a screen reader announces; the visible label
      // says where the button leads.
      aria-pressed={theme === 'dark'}
      className="rounded-md border border-border bg-surface px-3 py-2 text-sm text-text hover:bg-surface-raised"
    >
      Switch to {next} mode
    </button>
  );
}
