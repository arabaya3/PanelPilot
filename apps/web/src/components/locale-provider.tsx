'use client';

/**
 * Locale state, and the one place `lang` and `dir` are written.
 *
 * Direction is set on `<html>` and nowhere else. Every logical property in the
 * stylesheet — `margin-inline-start` rather than `margin-left` — flips from
 * that single attribute, which is what makes RTL a property of the document
 * rather than something each component re-implements.
 *
 * Retrofitting this later is what breaks half a component library: by then
 * forty components have `margin-left` in them and each needs its own override.
 */

import { NextIntlClientProvider } from 'next-intl';
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react';

import { DEFAULT_LOCALE, directionOf, toLocale, type Locale } from '@/i18n/config';

export const LOCALE_STORAGE_KEY = 'panelpilot-locale';

type Messages = Record<string, unknown>;

interface LocaleContextValue {
  locale: Locale;
  setLocale: (locale: Locale) => void;
  direction: 'ltr' | 'rtl';
}

const LocaleContext = createContext<LocaleContextValue | null>(null);

/**
 * The script that runs before React hydrates.
 *
 * Direction has to be right on the first paint. Unlike a colour, a page that
 * lays out left-to-right and then flips is not a flash — it is the whole
 * layout visibly rebuilding, and on a slow connection it lasts long enough to
 * read.
 */
export const localeInitScript = `
(function () {
  try {
    var stored = window.localStorage.getItem('${LOCALE_STORAGE_KEY}');
    var locale = stored === 'ar' || stored === 'he' || stored === 'en' ? stored : '${DEFAULT_LOCALE}';
    document.documentElement.setAttribute('lang', locale);
    document.documentElement.setAttribute('dir', locale === 'en' ? 'ltr' : 'rtl');
  } catch (e) {
    document.documentElement.setAttribute('lang', '${DEFAULT_LOCALE}');
    document.documentElement.setAttribute('dir', 'ltr');
  }
})();
`.trim();

function readStoredLocale(): Locale {
  try {
    return toLocale(window.localStorage.getItem(LOCALE_STORAGE_KEY));
  } catch {
    return DEFAULT_LOCALE;
  }
}

/**
 * Provide locale state and the message bundle for it.
 *
 * @param children - The application.
 * @param messages - Every locale's bundle, keyed by locale. All three are
 *   passed rather than fetched on switch: they are small, and a language
 *   change that shows a loading state is a language change that feels broken.
 * @param initialLocale - Overrides the stored value, for tests and stories.
 */
export function LocaleProvider({
  children,
  messages,
  initialLocale,
}: {
  children: ReactNode;
  messages: Record<Locale, Messages>;
  initialLocale?: Locale;
}) {
  const [locale, setLocaleState] = useState<Locale>(initialLocale ?? DEFAULT_LOCALE);

  useEffect(() => {
    if (initialLocale) return;
    setLocaleState(readStoredLocale());
  }, [initialLocale]);

  useEffect(() => {
    // Both attributes, together. `lang` without `dir` gives a screen reader
    // the right pronunciation and the browser the wrong layout; `dir` without
    // `lang` does the reverse.
    document.documentElement.setAttribute('lang', locale);
    document.documentElement.setAttribute('dir', directionOf(locale));
  }, [locale]);

  const setLocale = useCallback((next: Locale) => {
    setLocaleState(next);
    try {
      window.localStorage.setItem(LOCALE_STORAGE_KEY, next);
    } catch {
      // A language choice that does not survive a reload is worse than one
      // that does, and better than a switcher that appears to do nothing.
    }
  }, []);

  const value = useMemo(
    () => ({ locale, setLocale, direction: directionOf(locale) }),
    [locale, setLocale],
  );

  return (
    <LocaleContext.Provider value={value}>
      <NextIntlClientProvider locale={locale} messages={messages[locale]}>
        {children}
      </NextIntlClientProvider>
    </LocaleContext.Provider>
  );
}

/**
 * Read the current locale and its direction.
 *
 * @returns The locale, the setter, and the resolved direction.
 * @throws If called outside a `LocaleProvider` — loud, because a component
 *   silently defaulting to English while the page renders Arabic is far
 *   harder to trace than an error.
 */
export function useLocale(): LocaleContextValue {
  const context = useContext(LocaleContext);
  if (!context) {
    throw new Error('useLocale must be used inside a LocaleProvider');
  }
  return context;
}
