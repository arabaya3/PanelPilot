import { fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  DEFAULT_THEME,
  THEME_STORAGE_KEY,
  ThemeProvider,
  themeInitScript,
  useTheme,
} from '@/components/theme-provider';
import { ThemeToggle } from '@/components/theme-toggle';

/**
 * Execute the exact string the layout inlines.
 *
 * Testing a reimplementation of the script would pass while the shipped one
 * was broken, so this runs the real constant. Typed as returning void
 * because that is what the script does — it sets an attribute.
 */
function runInitScript(): void {
  // eslint-disable-next-line @typescript-eslint/no-implied-eval
  const run = new Function(themeInitScript) as () => void;
  run();
}

function ThemeReadout() {
  const { theme } = useTheme();
  return <span data-testid="theme">{theme}</span>;
}

beforeEach(() => {
  window.localStorage.clear();
  document.documentElement.removeAttribute('data-theme');
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('ThemeProvider', () => {
  it('writes the theme to the root element', () => {
    // The attribute is what both `tokens.css` and Tailwind's dark variant
    // read, so a provider that tracks state without writing it changes
    // nothing visible.
    render(
      <ThemeProvider initialTheme="dark">
        <ThemeReadout />
      </ThemeProvider>,
    );
    expect(document.documentElement.getAttribute('data-theme')).toBe('dark');
  });

  it('restores a persisted choice', () => {
    // A preference, not a session detail: someone who set dark mode on a
    // night shift should not set it again on the next call-out.
    window.localStorage.setItem(THEME_STORAGE_KEY, 'dark');
    render(
      <ThemeProvider>
        <ThemeReadout />
      </ThemeProvider>,
    );
    expect(screen.getByTestId('theme').textContent).toBe('dark');
  });

  it('ignores a corrupted stored value', () => {
    // Storage is shared with anything else on the origin and survives
    // deploys. A junk value must not render an unstyled page.
    window.localStorage.setItem(THEME_STORAGE_KEY, 'chartreuse');
    render(
      <ThemeProvider>
        <ThemeReadout />
      </ThemeProvider>,
    );
    expect(screen.getByTestId('theme').textContent).toBe(DEFAULT_THEME);
  });

  it('falls back to the default when storage throws', () => {
    // Private browsing and blocked cookies both land here. Neither is a
    // reason to show a broken page.
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('storage disabled');
    });
    render(
      <ThemeProvider>
        <ThemeReadout />
      </ThemeProvider>,
    );
    expect(screen.getByTestId('theme').textContent).toBe(DEFAULT_THEME);
  });

  it('still switches when storage cannot be written', () => {
    // Losing the preference on reload is a smaller failure than a toggle
    // that visibly does nothing.
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('storage full');
    });
    render(
      <ThemeProvider initialTheme="light">
        <ThemeToggle />
        <ThemeReadout />
      </ThemeProvider>,
    );
    fireEvent.click(screen.getByRole('button'));
    expect(screen.getByTestId('theme').textContent).toBe('dark');
  });

  it('persists a change', () => {
    render(
      <ThemeProvider initialTheme="light">
        <ThemeToggle />
      </ThemeProvider>,
    );
    fireEvent.click(screen.getByRole('button'));
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe('dark');
  });

  it('throws when used outside a provider', () => {
    // Loud on purpose. A silent default leaves a component reading `light`
    // while the page renders dark, which is far harder to trace than an error.
    const quiet = vi.spyOn(console, 'error').mockImplementation(() => {});
    expect(() => render(<ThemeReadout />)).toThrow(/ThemeProvider/);
    quiet.mockRestore();
  });
});

describe('themeInitScript', () => {
  it('sets the attribute before React runs', () => {
    // Without it the page paints light then flips — merely ugly on a desktop,
    // genuinely unpleasant on a phone held up to a dark panel at night.
    window.localStorage.setItem(THEME_STORAGE_KEY, 'dark');
    runInitScript();
    expect(document.documentElement.getAttribute('data-theme')).toBe('dark');
  });

  it('survives storage being unavailable', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('blocked');
    });
    runInitScript();
    expect(document.documentElement.getAttribute('data-theme')).toBe(DEFAULT_THEME);
  });
});

describe('ThemeToggle', () => {
  it('names the theme it will switch to', () => {
    // "Dark mode" on an already-dark button reads as a status, and people
    // click it expecting nothing to happen.
    render(
      <ThemeProvider initialTheme="light">
        <ThemeToggle />
      </ThemeProvider>,
    );
    expect(screen.getByRole('button').textContent).toContain('dark');
  });

  it('reports its state to assistive technology', () => {
    render(
      <ThemeProvider initialTheme="dark">
        <ThemeToggle />
      </ThemeProvider>,
    );
    expect(screen.getByRole('button').getAttribute('aria-pressed')).toBe('true');
  });

  it('round-trips', () => {
    render(
      <ThemeProvider initialTheme="light">
        <ThemeToggle />
        <ThemeReadout />
      </ThemeProvider>,
    );
    const button = screen.getByRole('button');
    fireEvent.click(button);
    expect(screen.getByTestId('theme').textContent).toBe('dark');
    fireEvent.click(button);
    expect(screen.getByTestId('theme').textContent).toBe('light');
  });
});
