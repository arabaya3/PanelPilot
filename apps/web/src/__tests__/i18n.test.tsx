import { render, screen, within } from '@testing-library/react';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { resolve } from 'node:path';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { DiagnosisSample } from '@/components/diagnosis-sample';
import { LangSwitcher } from '@/components/lang-switcher';
import {
  LOCALE_STORAGE_KEY,
  LocaleProvider,
  localeInitScript,
  useLocale,
} from '@/components/locale-provider';
import { TechnicalToken } from '@/components/technical-token';
import { DEFAULT_LOCALE, LOCALES, directionOf, toLocale, type Locale } from '@/i18n/config';
import ar from '@/messages/ar.json';
import en from '@/messages/en.json';
import he from '@/messages/he.json';

/**
 * Tests for the i18n infrastructure.
 *
 * The acceptance criterion has three parts and the middle one is the
 * substantive one: direction flips, **technical tokens stay LTR inside RTL
 * prose**, and nothing breaks at the boundary. A fault code reordered by the
 * bidirectional algorithm is not a cosmetic bug — "F0001" rendering as
 * "0001F" is the wrong code, and an engineer types what they see.
 */

const MESSAGES: Record<Locale, Record<string, unknown>> = { en, ar, he };

function renderIn(locale: Locale, children: React.ReactNode) {
  return render(
    <LocaleProvider messages={MESSAGES} initialLocale={locale}>
      {children}
    </LocaleProvider>,
  );
}

beforeEach(() => {
  window.localStorage.clear();
  document.documentElement.removeAttribute('dir');
  document.documentElement.removeAttribute('lang');
});

// --- direction --------------------------------------------------------------

describe('direction', () => {
  it.each([
    ['en', 'ltr'],
    ['ar', 'rtl'],
    ['he', 'rtl'],
  ] as const)('%s reads %s', (locale, expected) => {
    expect(directionOf(locale)).toBe(expected);
  });

  it.each(LOCALES)('sets lang and dir on the document for %s', (locale) => {
    // Both, together. `lang` without `dir` gives a screen reader the right
    // pronunciation and the browser the wrong layout; `dir` without `lang`
    // does the reverse.
    renderIn(locale, <span />);
    expect(document.documentElement.getAttribute('lang')).toBe(locale);
    expect(document.documentElement.getAttribute('dir')).toBe(directionOf(locale));
  });

  it('sets direction before React hydrates', () => {
    // Unlike a colour, a page that lays out LTR then flips is not a flash —
    // it is the whole layout visibly rebuilding.
    window.localStorage.setItem(LOCALE_STORAGE_KEY, 'ar');
    // eslint-disable-next-line @typescript-eslint/no-implied-eval
    (new Function(localeInitScript) as () => void)();
    expect(document.documentElement.getAttribute('dir')).toBe('rtl');
    expect(document.documentElement.getAttribute('lang')).toBe('ar');
  });

  it('falls back to the default when storage is unavailable', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('blocked');
    });
    // eslint-disable-next-line @typescript-eslint/no-implied-eval
    (new Function(localeInitScript) as () => void)();
    expect(document.documentElement.getAttribute('dir')).toBe('ltr');
    vi.restoreAllMocks();
  });

  it('narrows an unrecognised locale rather than throwing', () => {
    // An unfamiliar Accept-Language is ordinary; refusing over it would turn a
    // browser preference into an error page.
    expect(toLocale('fr')).toBe(DEFAULT_LOCALE);
    expect(toLocale(null)).toBe(DEFAULT_LOCALE);
    expect(toLocale('ar')).toBe('ar');
  });
});

// --- LTR islands inside RTL prose -------------------------------------------

describe('technical tokens', () => {
  it('isolates a token with bdi, not a bare dir', () => {
    // The difference matters: `bdi` also isolates the run, so the token cannot
    // reorder the text around it. A `<span dir="ltr">` fixes the token and
    // leaves the sentence it sits in scrambled.
    const { container } = render(<TechnicalToken>F0001</TechnicalToken>);
    const element = container.firstElementChild;
    expect(element?.tagName.toLowerCase()).toBe('bdi');
    expect(element?.getAttribute('dir')).toBe('ltr');
  });

  it('renders tokens in the mono stack', () => {
    // Transcribed by hand into a keypad; a proportional font makes 0/O and
    // 1/l ambiguous at exactly the moment that matters.
    const { container } = render(<TechnicalToken>21.03</TechnicalToken>);
    expect(container.firstElementChild?.className).toContain('font-mono');
  });

  it.each(LOCALES)('keeps every technical token LTR in %s', (locale) => {
    // The acceptance criterion, per locale. Each token must be inside a
    // dir="ltr" bdi regardless of the surrounding script.
    const { container } = renderIn(locale, <DiagnosisSample />);

    for (const token of ['F0001', 'ACS880', '21.03', '400 V']) {
      const node = screen.getByText(token);
      expect(node.tagName.toLowerCase(), `${token} in ${locale} is not a bdi`).toBe('bdi');
      expect(node.getAttribute('dir'), `${token} in ${locale} is not ltr`).toBe('ltr');
    }

    expect(container.querySelectorAll('bdi[dir="ltr"]')).toHaveLength(4);
  });

  it.each(['ar', 'he'] as const)('renders %s prose around the tokens', (locale) => {
    // The other half: if the surrounding sentence were still English, the
    // tokens would be trivially LTR and the test would prove nothing.
    const { container } = renderIn(locale, <DiagnosisSample />);
    const text = container.textContent;
    const script = locale === 'ar' ? /[؀-ۿ]/ : /[֐-׿]/;
    expect(script.test(text), `no ${locale} script in the rendered card`).toBe(true);
  });

  it('places a token wherever the translation puts it', () => {
    // Arabic puts the code early in the sentence, which is where reordering
    // risk is highest. A test that only ever saw it at the end would miss it.
    renderIn('ar', <DiagnosisSample />);
    const paragraph = screen.getByText('F0001').closest('p');
    expect(paragraph).toBeTruthy();
    expect(within(paragraph as HTMLElement).getByText('ACS880')).toBeTruthy();
  });
});

// --- the message bundles ----------------------------------------------------

describe('message bundles', () => {
  const bundles = LOCALES.map((locale) => [locale, MESSAGES[locale]] as const);

  /** Every leaf key path in a bundle. */
  function keyPaths(value: unknown, prefix = ''): string[] {
    if (typeof value !== 'object' || value === null) return [prefix];
    return Object.entries(value).flatMap(([key, child]) =>
      keyPaths(child, prefix ? `${prefix}.${key}` : key),
    );
  }

  it.each(bundles)('%s defines exactly the same keys as English', (locale, bundle) => {
    // A missing key renders as the key itself — visible, ugly, and shipped.
    // An extra key is a translation of something that no longer exists.
    expect(keyPaths(bundle).sort()).toEqual(keyPaths(en).sort());
    expect(locale).toBeTruthy();
  });

  it.each(bundles)('%s keeps every placeholder', (locale, bundle) => {
    // A dropped placeholder silently removes the fault code from the sentence.
    // The message still reads fine, and the engineer never sees the code —
    // which is the failure this whole task exists to prevent.
    //
    // Both forms are collected: `{value}` for simple interpolation and
    // `<tag>…</tag>` for the rich placeholders that carry a bdi wrapper.
    const placeholders = (input: unknown): Record<string, string[]> => {
      const found: Record<string, string[]> = {};
      const walk = (value: unknown, path: string) => {
        if (typeof value === 'string') {
          const simple = [...value.matchAll(/\{(\w+)\}/g)].map((m) => m[1] ?? '');
          const tags = [...value.matchAll(/<(\w+)>/g)].map((m) => m[1] ?? '');
          found[path] = [...simple, ...tags].sort();
          return;
        }
        if (typeof value === 'object' && value !== null) {
          for (const [key, child] of Object.entries(value)) {
            walk(child, path ? `${path}.${key}` : key);
          }
        }
      };
      walk(input, '');
      return found;
    };

    const expected = placeholders(en);
    const actual = placeholders(bundle);
    for (const [path, names] of Object.entries(expected)) {
      expect(actual[path], `${locale}: ${path}`).toEqual(names);
    }
  });

  it('never translates the product name', () => {
    // It is a proper noun and a trademark; transliterating it makes it a
    // different product in search and in conversation.
    for (const bundle of Object.values(MESSAGES)) {
      const app = (bundle as { app: { name: string } }).app;
      expect(app.name).toBe('PanelPilot');
    }
  });

  it('labels each language in its own script', () => {
    // Someone who cannot read the current interface is exactly the person
    // using the switcher.
    const languages = (en as { language: Record<string, string> }).language;
    expect(languages.ar).toMatch(/[؀-ۿ]/);
    expect(languages.he).toMatch(/[֐-׿]/);
  });
});

// --- the switcher -----------------------------------------------------------

describe('LangSwitcher', () => {
  it('offers every supported locale', () => {
    renderIn('en', <LangSwitcher />);
    const values = screen
      .getAllByRole('option')
      .map((option) => (option as HTMLOptionElement).value);
    expect(values.sort()).toEqual([...LOCALES].sort());
  });

  it('marks each option with its own language', () => {
    // So a screen reader pronounces the name in the language it is written
    // in, rather than in the page's language.
    renderIn('en', <LangSwitcher />);
    for (const option of screen.getAllByRole('option')) {
      expect(option.getAttribute('lang')).toBeTruthy();
    }
  });

  it('throws when used outside a provider', () => {
    const quiet = vi.spyOn(console, 'error').mockImplementation(() => {});
    function Bare() {
      useLocale();
      return null;
    }
    expect(() => render(<Bare />)).toThrow(/LocaleProvider/);
    quiet.mockRestore();
  });
});

// --- logical properties -----------------------------------------------------

describe('layout direction', () => {
  it('uses no physical direction properties in components', () => {
    // Asserted here as well as in the build script, so the rule survives
    // someone removing the script from `lint`. A `margin-left` does not flip
    // under dir="rtl", and the bug surfaces as "the spacing looks odd" long
    // after whoever wrote it moved on.
    const sources = ['src/components', 'src/app'].flatMap((directory) =>
      collectSources(resolve(process.cwd(), directory)),
    );
    const offenders: string[] = [];
    for (const { path, text } of sources) {
      for (const match of text.matchAll(/\b(?:ml|mr|pl|pr)-(?:\d+|px|auto)\b/g)) {
        offenders.push(`${path}: ${match[0]}`);
      }
    }
    expect(offenders).toEqual([]);
  });
});

function collectSources(directory: string): { path: string; text: string }[] {
  const out: { path: string; text: string }[] = [];
  for (const entry of readdirSync(directory)) {
    const full = resolve(directory, entry);
    if (statSync(full).isDirectory()) {
      out.push(...collectSources(full));
      continue;
    }
    if (entry.endsWith('.tsx') || entry.endsWith('.css')) {
      out.push({ path: entry, text: readFileSync(full, 'utf8') });
    }
  }
  return out;
}
