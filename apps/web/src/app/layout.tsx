import { Noto_Sans, Noto_Sans_Arabic, Noto_Sans_Hebrew } from 'next/font/google';
import type { ReactNode } from 'react';

import { LocaleProvider, localeInitScript } from '@/components/locale-provider';
import { ThemeProvider, themeInitScript } from '@/components/theme-provider';
import ar from '@/messages/ar.json';
import en from '@/messages/en.json';
import he from '@/messages/he.json';
import '@/styles/globals.css';

/**
 * One family per script, all three loaded as CSS variables.
 *
 * Noto Sans, Noto Sans Arabic and Noto Sans Hebrew share a design and an
 * x-height, so switching language changes the words without shifting the
 * rhythm of the page — a different-metric fallback makes every switch look
 * like a layout bug.
 *
 * All three load rather than one per locale, because the language switcher is
 * client-side: fetching a font at switch time would show the new language in
 * a fallback face for a moment, which reads as breakage.
 */
const notoSans = Noto_Sans({
  subsets: ['latin'],
  variable: '--font-noto-sans',
  display: 'swap',
});

const notoSansArabic = Noto_Sans_Arabic({
  subsets: ['arabic'],
  variable: '--font-noto-arabic',
  display: 'swap',
});

const notoSansHebrew = Noto_Sans_Hebrew({
  subsets: ['hebrew'],
  variable: '--font-noto-hebrew',
  display: 'swap',
});

export const metadata = {
  title: 'PanelPilot',
  description: 'Diagnostic and design copilot for electrical and control engineers.',
};

const MESSAGES = { en, ar, he };

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    // `lang` and `dir` are placeholders here and authoritative on the client:
    // the inline script below sets both before paint, and LocaleProvider keeps
    // them in step. `suppressHydrationWarning` covers exactly that — the
    // server cannot know a preference stored in the browser.
    <html
      lang="en"
      dir="ltr"
      suppressHydrationWarning
      className={`${notoSans.variable} ${notoSansArabic.variable} ${notoSansHebrew.variable}`}
    >
      <head>
        {/* Both run before first paint. Theme prevents a colour flash;
            direction prevents the whole layout visibly rebuilding, which on a
            slow connection lasts long enough to read. The content of each is
            a module-level constant — no user input reaches them. */}
        <script dangerouslySetInnerHTML={{ __html: themeInitScript }} />
        <script dangerouslySetInnerHTML={{ __html: localeInitScript }} />
      </head>
      <body>
        <ThemeProvider>
          <LocaleProvider messages={MESSAGES}>{children}</LocaleProvider>
        </ThemeProvider>
      </body>
    </html>
  );
}
