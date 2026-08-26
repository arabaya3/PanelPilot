'use client';

/**
 * Theme state, and the one place `data-theme` is written.
 *
 * The attribute drives both the CSS variables in `tokens.css` and Tailwind's
 * dark variant, so a single write switches the whole application. Components
 * never read the theme to pick a colour — they use a token, and the token
 * changes underneath them.
 *
 * The choice is persisted because it is a preference, not a session detail: an
 * engineer who set dark mode at 2am on a night shift should not have to set it
 * again on the next call-out.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react';

export type Theme = 'light' | 'dark';

export const THEME_STORAGE_KEY = 'panelpilot-theme';
export const DEFAULT_THEME: Theme = 'light';

interface ThemeContextValue {
  theme: Theme;
  setTheme: (theme: Theme) => void;
  toggleTheme: () => void;
}

const ThemeContext = createContext<ThemeContextValue | null>(null);

/**
 * Read the persisted theme, tolerating every way storage can fail.
 *
 * Private browsing, disabled cookies and a corrupted value all end up here,
 * and none of them is a reason to show a broken page — so any failure falls
 * back to the default rather than propagating.
 */
function readStoredTheme(): Theme {
  try {
    const stored = window.localStorage.getItem(THEME_STORAGE_KEY);
    return stored === 'dark' || stored === 'light' ? stored : DEFAULT_THEME;
  } catch {
    return DEFAULT_THEME;
  }
}

/**
 * The script that runs before React hydrates.
 *
 * Without it the page paints light, then flips to dark once JavaScript loads —
 * a flash that is merely ugly on a desktop and genuinely unpleasant on a phone
 * held up to a dark panel at night. It is deliberately tiny and inlined: a
 * separate request would be slower than the paint it is trying to beat.
 */
export const themeInitScript = `
(function () {
  try {
    var stored = window.localStorage.getItem('${THEME_STORAGE_KEY}');
    var theme = stored === 'dark' || stored === 'light' ? stored : '${DEFAULT_THEME}';
    document.documentElement.setAttribute('data-theme', theme);
  } catch (e) {
    document.documentElement.setAttribute('data-theme', '${DEFAULT_THEME}');
  }
})();
`.trim();

/**
 * Provide theme state to the application.
 *
 * @param children - The application.
 * @param initialTheme - Overrides the stored value. For tests and stories,
 *   which need a deterministic theme rather than whatever the last run left
 *   in storage.
 */
export function ThemeProvider({
  children,
  initialTheme,
}: {
  children: ReactNode;
  initialTheme?: Theme;
}) {
  // Initialised to the default rather than to storage, so the server render
  // and the first client render agree. The effect below corrects it, and the
  // inline script has already set the attribute so nothing flashes.
  const [theme, setThemeState] = useState<Theme>(initialTheme ?? DEFAULT_THEME);

  useEffect(() => {
    if (initialTheme) return;
    setThemeState(readStoredTheme());
  }, [initialTheme]);

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme);
  }, [theme]);

  const setTheme = useCallback((next: Theme) => {
    setThemeState(next);
    try {
      window.localStorage.setItem(THEME_STORAGE_KEY, next);
    } catch {
      // Storage being unavailable must not stop the theme changing for this
      // session. The preference is lost on reload, which is a smaller failure
      // than a toggle that does nothing.
    }
  }, []);

  const toggleTheme = useCallback(() => {
    setTheme(theme === 'dark' ? 'light' : 'dark');
  }, [theme, setTheme]);

  const value = useMemo(() => ({ theme, setTheme, toggleTheme }), [theme, setTheme, toggleTheme]);

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

/**
 * Read the current theme.
 *
 * @returns The theme and the setters for it.
 * @throws If called outside a `ThemeProvider`. Deliberately loud: a silent
 *   default would leave a component reading `light` while the page renders
 *   dark, and that mismatch is far harder to trace than a thrown error.
 */
export function useTheme(): ThemeContextValue {
  const context = useContext(ThemeContext);
  if (!context) {
    throw new Error('useTheme must be used inside a ThemeProvider');
  }
  return context;
}
