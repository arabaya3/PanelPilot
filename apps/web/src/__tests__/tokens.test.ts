import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { describe, expect, it } from 'vitest';

/**
 * Tests for the token file itself.
 *
 * A review found the whole suite passed with `tokens.css` emptied to a single
 * comment, and with Tailwind remapped to a literal hex — the entire deliverable
 * of FE-001 had no regression protection at all. These are the tests that make
 * the token system falsifiable.
 *
 * The contrast numbers matter most. The first version of this file carried a
 * comment claiming contrast "is checked", and the focus ring scored 1.06:1
 * against the accent button — invisible, on the affordance a keyboard user
 * depends on. A claim in a comment is not a check; this is.
 */

// Resolved from the workspace root rather than from import.meta.url: Vite
// rewrites that to an http URL during a test run, and the files being read
// here are source on disk, not modules.
const TOKENS_PATH = resolve(process.cwd(), 'src/styles/tokens.css');
const TAILWIND_PATH = resolve(process.cwd(), 'tailwind.config.ts');

const tokensSource = readFileSource(TOKENS_PATH);
const tailwindSource = readFileSource(TAILWIND_PATH);

function readFileSource(path: string): string {
  return readFileSync(path, 'utf8');
}

/** Parse one theme block into a name → value map. */
function parseTheme(selector: string): Record<string, string> {
  const start = tokensSource.indexOf(selector);
  if (start === -1) throw new Error(`no ${selector} block in tokens.css`);
  const open = tokensSource.indexOf('{', start);
  const close = tokensSource.indexOf('\n}', open);
  const body = tokensSource.slice(open, close);

  const found: Record<string, string> = {};
  for (const match of body.matchAll(/(--[a-z0-9-]+)\s*:\s*([^;]+);/g)) {
    const [, name, value] = match;
    if (name && value) found[name] = value.trim();
  }
  return found;
}

const LIGHT = parseTheme(':root {');
const DARK = parseTheme(":root[data-theme='dark']");

/** Relative luminance, per WCAG 2.1. */
function luminance(hex: string): number {
  const value = hex.replace('#', '');
  const [r = 0, g = 0, b = 0] = [0, 2, 4]
    .map((offset) => parseInt(value.slice(offset, offset + 2), 16) / 255)
    .map((c) => (c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4)));
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

/**
 * Read a token, failing loudly when it is absent.
 *
 * A missing token silently reading as `undefined` would make a contrast
 * assertion compare against `NaN` and pass, which is the opposite of what
 * these tests are for.
 */
function token(theme: Record<string, string>, name: string): string {
  const value = theme[name];
  if (!value) throw new Error(`${name} is not defined`);
  return value;
}

/** WCAG contrast ratio between two hex colours. */
function contrast(a: string, b: string): number {
  const [first, second] = [luminance(a), luminance(b)];
  const [lighter, darker] = first > second ? [first, second] : [second, first];
  return (lighter + 0.05) / (darker + 0.05);
}

const THEMES: ReadonlyArray<readonly [string, Record<string, string>]> = [
  ['light', LIGHT],
  ['dark', DARK],
];

// --- the tokens the rest of the product is built from -----------------------

const REQUIRED_TOKENS = [
  '--color-bg',
  '--color-surface',
  '--color-surface-raised',
  '--color-border',
  '--color-text',
  '--color-text-muted',
  '--color-severity-critical',
  '--color-severity-warning',
  '--color-severity-info',
  '--color-severity-critical-surface',
  '--color-severity-warning-surface',
  '--color-severity-info-surface',
  '--color-accent',
  '--color-accent-hover',
  '--color-accent-contrast',
  '--color-focus',
  '--color-focus-offset',
];

describe('tokens.css', () => {
  it('defines every token the product references', () => {
    // Emptying this file used to leave the suite green. It no longer does.
    for (const token of REQUIRED_TOKENS) {
      expect(LIGHT[token], `${token} missing from the light theme`).toBeTruthy();
    }
  });

  it('redefines every colour token in dark mode', () => {
    // A token defined only in light silently keeps its light value in dark —
    // which is how a "dark mode" ends up with one white card in it.
    for (const token of REQUIRED_TOKENS) {
      expect(DARK[token], `${token} is not redefined for dark mode`).toBeTruthy();
    }
  });

  it('defines the spacing scale on a 4px base', () => {
    // Paired explicitly rather than indexed, so the step names are strings and
    // a mismatch names the token rather than a position.
    const expected: ReadonlyArray<readonly [string, string]> = [
      ['1', '0.25rem'],
      ['2', '0.5rem'],
      ['3', '0.75rem'],
      ['4', '1rem'],
      ['5', '1.5rem'],
      ['6', '2rem'],
      ['7', '3rem'],
      ['8', '4rem'],
    ];
    for (const [step, value] of expected) {
      expect(token(LIGHT, `--space-${step}`), `--space-${step}`).toBe(value);
    }
  });

  it('defines the whole type scale', () => {
    for (const step of ['xs', 'sm', 'base', 'lg', 'xl', '2xl']) {
      expect(LIGHT[`--font-size-${step}`], `--font-size-${step} missing`).toBeTruthy();
    }
  });

  it('provides a mono stack for technical values', () => {
    // Codes and measurements are transcribed by hand into a keypad, and a
    // proportional font makes 0/O and 1/l ambiguous.
    expect(token(LIGHT, '--font-mono')).toContain('mono');
  });

  it('names fonts covering the three supported scripts', () => {
    // Latin, Arabic and Hebrew all render in this stack. A missing fallback
    // means an Arabic answer renders in whatever the OS picks.
    expect(token(LIGHT, '--font-sans')).toContain('Arabic');
    expect(token(LIGHT, '--font-sans')).toContain('Hebrew');
  });
});

// --- contrast, measured rather than claimed ---------------------------------

describe('contrast', () => {
  it.each(THEMES)('%s: body text clears AA on every surface', (_name, theme) => {
    for (const surface of ['--color-bg', '--color-surface', '--color-surface-raised']) {
      expect(contrast(token(theme, '--color-text'), token(theme, surface))).toBeGreaterThanOrEqual(
        4.5,
      );
    }
  });

  it.each(THEMES)('%s: muted text clears AA on every surface', (_name, theme) => {
    // Including surface-raised. The first version checked only `surface` and
    // scored 4.04:1 on raised, below AA — the comment said contrast was
    // checked, and it had been, against one of the three.
    for (const surface of ['--color-bg', '--color-surface', '--color-surface-raised']) {
      expect(
        contrast(token(theme, '--color-text-muted'), token(theme, surface)),
      ).toBeGreaterThanOrEqual(4.5);
    }
  });

  it.each(THEMES)('%s: each severity clears AA on its own surface', (_name, theme) => {
    for (const level of ['critical', 'warning', 'info']) {
      const ratio = contrast(
        token(theme, `--color-severity-${level}`),
        token(theme, `--color-severity-${level}-surface`),
      );
      expect(ratio, `${level} on its surface`).toBeGreaterThanOrEqual(4.5);
    }
  });

  it.each(THEMES)('%s: severity is readable on the plain surface too', (_name, theme) => {
    // A severity colour is used as text on a card as often as on its own
    // tinted background.
    for (const level of ['critical', 'warning', 'info']) {
      const ratio = contrast(
        token(theme, `--color-severity-${level}`),
        token(theme, '--color-surface'),
      );
      expect(ratio, `${level} on surface`).toBeGreaterThanOrEqual(4.5);
    }
  });

  it.each(THEMES)('%s: accent text is readable on the accent', (_name, theme) => {
    expect(
      contrast(token(theme, '--color-accent-contrast'), token(theme, '--color-accent')),
    ).toBeGreaterThanOrEqual(4.5);
  });

  it.each(THEMES)('%s: borders meet the 3:1 UI boundary threshold', (_name, theme) => {
    // WCAG 1.4.11. A border separating two cards is a component boundary, and
    // the first version scored 1.48:1 — a suggestion of an edge, not an edge.
    for (const surface of ['--color-bg', '--color-surface', '--color-surface-raised']) {
      expect(
        contrast(token(theme, '--color-border'), token(theme, surface)),
      ).toBeGreaterThanOrEqual(3);
    }
  });

  it.each(THEMES)('%s: the focus ring is visible against every surface', (_name, theme) => {
    // Including the accent button, which is where the first version failed at
    // 1.06:1 — invisible, on the affordance a keyboard user depends on.
    //
    // The ring reads against the offset, and the offset reads against
    // whatever is behind it, so a focused element is outlined whatever it
    // sits on.
    expect(
      contrast(token(theme, '--color-focus'), token(theme, '--color-focus-offset')),
      'ring against its own offset',
    ).toBeGreaterThanOrEqual(3);

    for (const behind of ['--color-bg', '--color-surface', '--color-accent']) {
      const ringOrOffset = Math.max(
        contrast(token(theme, '--color-focus'), token(theme, behind)),
        contrast(token(theme, '--color-focus-offset'), token(theme, behind)),
      );
      expect(ringOrOffset, `focus ring on ${behind}`).toBeGreaterThanOrEqual(3);
    }
  });
});

// --- Tailwind resolves to the tokens, not to its own palette ----------------

describe('tailwind.config.ts', () => {
  it('maps every colour to a CSS variable', () => {
    // Remapping one to a literal hex used to leave the suite green, which
    // would silently un-token whichever component used it.
    const colours = tailwindSource.slice(
      tailwindSource.indexOf('colors: {'),
      tailwindSource.indexOf('spacing: {'),
    );
    expect(colours).not.toMatch(/#[0-9a-fA-F]{3,8}/);
    for (const token of ['--color-surface', '--color-text', '--color-accent', '--color-focus']) {
      expect(colours, `${token} not referenced`).toContain(token);
    }
  });

  it('replaces the default palette rather than extending it', () => {
    // `theme.extend.colors` would leave `bg-slate-100` available, and a token
    // system you can opt out of is one half the components ignore.
    const themeBlock = tailwindSource.slice(tailwindSource.indexOf('theme: {'));
    const extendAt = themeBlock.indexOf('extend: {');
    const coloursAt = themeBlock.indexOf('colors: {');
    expect(coloursAt).toBeGreaterThan(-1);
    expect(coloursAt).toBeLessThan(extendAt);
  });

  it('maps spacing and type to variables too', () => {
    expect(tailwindSource).toContain('var(--space-1)');
    expect(tailwindSource).toContain('var(--font-size-xs)');
    expect(tailwindSource).toContain('var(--font-mono)');
  });
});
