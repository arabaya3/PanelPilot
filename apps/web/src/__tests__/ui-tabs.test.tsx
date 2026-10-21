import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { Tabs } from '@/components/ui';

/**
 * A tab list has exactly one tab in the page's tab order. A disabled button
 * cannot take focus, so when the selected tab is disabled another tab has to
 * be the tab stop, or the list cannot be reached by keyboard at all.
 */
function tabStops(): string[] {
  return screen
    .getAllByRole('tab')
    .filter((tab) => tab.getAttribute('tabindex') === '0')
    .map((tab) => tab.textContent);
}

describe('Tabs', () => {
  it('makes the selected tab the only tab stop', () => {
    render(
      <Tabs
        items={[
          { key: 'a', label: 'Alpha' },
          { key: 'b', label: 'Beta' },
        ]}
        selected="b"
        onSelect={() => {}}
        label="Example"
        idBase="t1"
      />,
    );
    expect(tabStops()).toEqual(['Beta']);
  });

  it('falls back to the first enabled tab when the selected one is disabled', () => {
    render(
      <Tabs
        items={[
          { key: 'a', label: 'Alpha', disabled: true },
          { key: 'b', label: 'Beta' },
          { key: 'c', label: 'Gamma' },
        ]}
        selected="a"
        onSelect={() => {}}
        label="Example"
        idBase="t2"
      />,
    );
    expect(tabStops()).toEqual(['Beta']);
    // Still the selected tab for assistive technology, even though it is not focusable.
    expect(screen.getByRole('tab', { name: 'Alpha' }).getAttribute('aria-selected')).toBe('true');
  });
});
