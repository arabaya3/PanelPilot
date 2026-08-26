import type { ReactNode } from 'react';

import { ThemeProvider, themeInitScript } from '@/components/theme-provider';
import '@/styles/globals.css';

export const metadata = {
  title: 'PanelPilot',
  description: 'Diagnostic and design copilot for electrical and control engineers.',
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    // `suppressHydrationWarning` because the inline script below sets
    // `data-theme` before React hydrates, so the server markup and the DOM
    // legitimately differ on exactly that attribute. Scoped to <html> — a
    // blanket suppression would hide real mismatches.
    <html lang="en" suppressHydrationWarning>
      <head>
        {/* Runs before first paint so a dark-mode user never sees a white
            flash. The content is a module-level constant — no user input
            reaches it, which is what makes raw HTML acceptable here and
            nowhere else. */}
        <script dangerouslySetInnerHTML={{ __html: themeInitScript }} />
      </head>
      <body>
        <ThemeProvider>{children}</ThemeProvider>
      </body>
    </html>
  );
}
