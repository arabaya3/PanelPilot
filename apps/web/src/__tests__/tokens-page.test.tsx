import { screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { renderApp } from './helpers';

import TokensPage from '@/app/tokens/page';

/**
 * Tests for the token gallery.
 *
 * The acceptance criterion calls this page the visual-regression baseline for
 * every later component task. A review pointed out it had no coverage at all —
 * the `data-testid` attributes existed for tests nobody had written, which
 * reads as coverage to anyone skimming the file.
 *
 * These assert the gallery actually shows every token. A baseline that
 * silently stops rendering half the palette is worse than no baseline, because
 * the next nine tasks are checked against it.
 */

function renderGallery(theme: 'light' | 'dark' = 'light') {
  return renderApp(
    <>
      <TokensPage />
    </>,
    { theme },
  );
}

describe('the token gallery', () => {
  it('shows every surface token', () => {
    renderGallery();
    for (const token of [
      '--color-bg',
      '--color-surface',
      '--color-surface-raised',
      '--color-border',
    ]) {
      expect(screen.getByTestId(`swatch-${token}`), `${token} missing`).toBeTruthy();
    }
  });

  it('shows every severity level', () => {
    // Three, and an engineer learns the colour once — so all three have to be
    // visible side by side to be comparable. One render, three lookups: a
    // render per iteration stacks galleries and every lookup then matches
    // several.
    renderGallery();
    for (const level of ['critical', 'warning', 'info']) {
      expect(screen.getByTestId(`severity-${level}`), `${level} missing`).toBeTruthy();
      expect(screen.getByText(level.charAt(0).toUpperCase() + level.slice(1))).toBeTruthy();
    }
  });

  it('shows the whole spacing scale', () => {
    renderGallery();
    // String steps rather than numbers: the lint rule forbidding implicit
    // number-to-string conversion in a template is right, and the page's own
    // SPACES constant is strings for the same reason.
    for (const step of ['1', '2', '3', '4', '5', '6', '7', '8']) {
      expect(screen.getByTestId(`space-${step}`), `--space-${step} missing`).toBeTruthy();
    }
  });

  it('names each token beside its swatch', () => {
    // A swatch without its name is a colour nobody can look up, which makes
    // the gallery decorative rather than usable.
    renderGallery();
    // `getAllByText` rather than `getByText`: `--color-surface` is a prefix of
    // `--color-surface-raised`, so a substring match legitimately finds both
    // and the singular form fails on the ambiguity rather than on absence.
    for (const token of ['--color-severity-critical', '--color-surface', '--space-4']) {
      expect(
        screen.getAllByText(token, { exact: false }).length,
        `${token} unlabelled`,
      ).toBeGreaterThan(0);
    }
  });

  it('demonstrates the type scale', () => {
    renderGallery();
    // Six steps, each rendered with real text rather than a label, so the
    // relative sizes are actually comparable.
    const samples = screen.getAllByText(/the drive tripped on overcurrent/);
    expect(samples).toHaveLength(6);
  });

  it('shows technical values in the mono stack', () => {
    renderGallery();
    for (const value of ['F0001', '21.03', '400 V']) {
      expect(screen.getByText(value), `${value} missing`).toBeTruthy();
    }
  });

  it('offers the theme toggle', () => {
    // The page is the manual check for dark mode, so it has to be switchable
    // from the page itself.
    renderGallery();
    expect(screen.getByRole('button', { name: /switch to/i })).toBeTruthy();
  });

  it('renders the same swatches in both themes', () => {
    // Nothing on this page branches on the theme — the tokens change
    // underneath it, which is the whole point of the token design.
    //
    // The toggle's own label is the one legitimate difference, since it names
    // the theme it will switch TO. So the comparison is over the swatches
    // rather than the whole document.
    const swatchIds = (container: HTMLElement) =>
      Array.from(container.querySelectorAll('[data-testid]'))
        .map((node) => node.getAttribute('data-testid'))
        .sort();

    const first = renderGallery('light');
    const light = swatchIds(first.container);
    first.unmount();

    const dark = swatchIds(renderGallery('dark').container);
    expect(light).toEqual(dark);
    expect(light.length).toBeGreaterThan(10);
  });
});
