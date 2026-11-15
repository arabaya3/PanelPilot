import { screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { ExportPanel } from '@/components/design/design-result';

import { renderApp } from './helpers';

/**
 * Found in the M8 browser pass: at 390 px the JSON download button's long
 * label ("Design data (JSON): calculated design and findings, not the saved
 * configuration") kept to one line and widened the page to 626 px. Every
 * export button may wrap now (layout itself is checked in the browser).
 */
describe('export buttons on a narrow screen', () => {
  it('may wrap instead of widening the page', () => {
    renderApp(<ExportPanel onExport={vi.fn()} exportError={null} testIdPrefix="t" />);
    const buttons = screen
      .getAllByRole('button')
      .filter((button) => (button.getAttribute('data-testid') ?? '').startsWith('t-'));
    expect(buttons.length).toBeGreaterThan(0);
    for (const button of buttons) {
      expect(button.className).toContain('whitespace-normal');
      expect(button.className).toContain('max-w-full');
    }
    expect(screen.getByTestId('t-json').textContent).toContain('not the saved configuration');
  });
});
