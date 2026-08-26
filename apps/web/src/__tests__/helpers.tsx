import { render } from '@testing-library/react';
import type { ReactNode } from 'react';

import { LocaleProvider } from '@/components/locale-provider';
import { ThemeProvider, type Theme } from '@/components/theme-provider';
import type { Locale } from '@/i18n/config';
import ar from '@/messages/ar.json';
import en from '@/messages/en.json';
import he from '@/messages/he.json';

/**
 * The provider stack every component sees in the real application.
 *
 * Tests compose it from here rather than each assembling its own, so that
 * adding a provider is one edit instead of one per test file — and, more to
 * the point, so a component that starts depending on translations cannot pass
 * its own tests while failing in a page that renders it without the context.
 */
export const MESSAGES: Record<Locale, Record<string, unknown>> = { en, ar, he };

export function renderApp(
  ui: ReactNode,
  { theme, locale = 'en' }: { theme?: Theme; locale?: Locale } = {},
) {
  // `theme` is deliberately not defaulted. Passing `initialTheme` pins the
  // theme and bypasses the stored preference, so a helper that always passed
  // one would quietly disable the storage-restore tests — they would render,
  // assert, and pass against a provider that never read storage at all.
  return render(
    <LocaleProvider messages={MESSAGES} initialLocale={locale}>
      {theme ? (
        <ThemeProvider initialTheme={theme}>{ui}</ThemeProvider>
      ) : (
        <ThemeProvider>{ui}</ThemeProvider>
      )}
    </LocaleProvider>,
  );
}
