/**
 * Tailwind mapped onto the design tokens.
 *
 * Every scale here resolves to a CSS custom property from `tokens.css`, so
 * `bg-surface` and `var(--color-surface)` are the same value and a theme
 * switch moves both. Tailwind's own palette is deliberately **replaced**
 * rather than extended for colour: leaving `bg-slate-100` available would
 * make the token system optional, and an optional design system is one that
 * half the components ignore.
 */

import type { Config } from 'tailwindcss';

const config: Config = {
  content: ['./src/**/*.{ts,tsx}'],
  // Class strategy, not media: the theme follows the user's explicit choice,
  // which `ThemeProvider` records on the root element.
  darkMode: ['class', '[data-theme="dark"]'],
  theme: {
    // `colors` rather than `extend.colors` — this is the whole palette.
    colors: {
      transparent: 'transparent',
      current: 'currentColor',
      inherit: 'inherit',

      bg: 'var(--color-bg)',
      surface: {
        DEFAULT: 'var(--color-surface)',
        raised: 'var(--color-surface-raised)',
      },
      // The navigation rail and top bar.
      chrome: 'var(--color-chrome)',
      border: {
        DEFAULT: 'var(--color-border)',
        subtle: 'var(--color-border-subtle)',
      },
      text: {
        DEFAULT: 'var(--color-text)',
        muted: 'var(--color-text-muted)',
      },
      severity: {
        critical: 'var(--color-severity-critical)',
        warning: 'var(--color-severity-warning)',
        info: 'var(--color-severity-info)',
        'critical-surface': 'var(--color-severity-critical-surface)',
        'warning-surface': 'var(--color-severity-warning-surface)',
        'info-surface': 'var(--color-severity-info-surface)',
      },
      // An alias, not a fourth severity: screens wrote `text-danger` for an
      // error line before any `danger` colour existed, and Tailwind silently
      // generated nothing. It resolves to the critical severity.
      danger: 'var(--color-severity-critical)',
      success: {
        DEFAULT: 'var(--color-success)',
        surface: 'var(--color-success-surface)',
      },
      // Paper and ink for drawing frames, which stay paper in both themes.
      sheet: {
        DEFAULT: 'var(--color-sheet)',
        ink: 'var(--color-sheet-ink)',
      },
      accent: {
        DEFAULT: 'var(--color-accent)',
        hover: 'var(--color-accent-hover)',
        contrast: 'var(--color-accent-contrast)',
        subtle: 'var(--color-accent-subtle)',
      },
      focus: 'var(--color-focus)',
    },
    spacing: {
      0: '0',
      1: 'var(--space-1)',
      2: 'var(--space-2)',
      3: 'var(--space-3)',
      4: 'var(--space-4)',
      5: 'var(--space-5)',
      6: 'var(--space-6)',
      7: 'var(--space-7)',
      8: 'var(--space-8)',
      px: '1px',
    },
    fontSize: {
      xs: 'var(--font-size-xs)',
      sm: 'var(--font-size-sm)',
      base: 'var(--font-size-base)',
      lg: 'var(--font-size-lg)',
      xl: 'var(--font-size-xl)',
      '2xl': 'var(--font-size-2xl)',
      '3xl': 'var(--font-size-3xl)',
    },
    fontFamily: {
      sans: 'var(--font-sans)',
      mono: 'var(--font-mono)',
    },
    borderRadius: {
      none: '0',
      sm: 'var(--radius-sm)',
      md: 'var(--radius-md)',
      lg: 'var(--radius-lg)',
      xl: 'var(--radius-xl)',
      full: '9999px',
    },
    boxShadow: {
      none: 'none',
      sm: 'var(--shadow-sm)',
      md: 'var(--shadow-md)',
      lg: 'var(--shadow-lg)',
    },
    // Box sizes, which are not spacing. Replacing `spacing` above also removed
    // Tailwind's default width and height scale, since both inherit from it,
    // and a class naming a missing key does not fail -- it generates nothing.
    // `w-64` on the history sidebar was silently dropped, so the sidebar sized
    // itself to its longest title and squeezed the conversation to a sliver.
    // Only the sizes in use, at Tailwind's own values; `tailwind-classes.test`
    // fails on any other.
    extend: {
      width: { 20: '5rem', 24: '6rem', 40: '10rem', 56: '14rem', 64: '16rem', 72: '18rem' },
      height: { 96: '24rem' },
      minWidth: { 14: '3.5rem', 32: '8rem', 40: '10rem', 48: '12rem', 64: '16rem' },
      maxHeight: { 40: '10rem', 64: '16rem', 96: '24rem' },
      minHeight: { 96: '24rem' },
    },
  },
  plugins: [],
};

export default config;
