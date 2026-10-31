import { screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { PlcPanel } from '@/components/plc-panel';

import { renderApp } from './helpers';

/** The control program under a designed board: its files are refused, so the panel says why. */

describe('plc panel', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('shows the program and the I/O list as not available, and why', () => {
    renderApp(<PlcPanel />);
    for (const testId of ['plc-export-st', 'plc-export-io']) {
      const button = screen.getByTestId(testId);
      expect((button as HTMLButtonElement).disabled).toBe(true);
      expect(button.getAttribute('aria-describedby')).toBe(
        screen.getByTestId('plc-unavailable').id,
      );
    }
    expect(screen.getByTestId('plc-unavailable').textContent).toBe(
      'Not available yet: this format cannot keep a visible draft warning once imported. Use the PDF or DXF for review.',
    );
  });

  it('neither asks for the program nor shows any of its source', () => {
    const fetchSpy = vi.fn();
    vi.stubGlobal('fetch', fetchSpy);
    renderApp(<PlcPanel />);
    expect(fetchSpy).not.toHaveBeenCalled();
    expect(screen.queryByRole('button', { name: 'Write the program' })).toBeNull();
    expect(screen.queryByTestId('plc-source')).toBeNull();
    expect(screen.getByTestId('plc-panel').querySelector('pre, code, textarea')).toBeNull();
  });
});
