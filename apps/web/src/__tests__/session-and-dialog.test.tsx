import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { Dialog } from '@/components/ui';
import { clearTrial, DESIGN_DRAFT_STORAGE_KEY } from '@/lib/trial';

describe('ending the trial', () => {
  it('removes the unsaved design draft that belonged to it', () => {
    window.sessionStorage.setItem(DESIGN_DRAFT_STORAGE_KEY, '{"version":1}');
    clearTrial();
    expect(window.sessionStorage.getItem(DESIGN_DRAFT_STORAGE_KEY)).toBeNull();
  });
});

describe('a dialog backdrop', () => {
  function renderOpen(onClose: () => void) {
    render(
      <Dialog open onClose={onClose} title="Discard?" closeLabel="Close">
        body
      </Dialog>,
    );
    // The backdrop is the dialog element itself.
    return screen.getByRole('dialog', { hidden: true });
  }

  it('ignores the second click of the double click that opened it', () => {
    vi.useFakeTimers({ toFake: ['Date'] });
    try {
      const onClose = vi.fn();
      const backdrop = renderOpen(onClose);
      vi.setSystemTime(Date.now() + 120);
      fireEvent.click(backdrop);
      expect(onClose).not.toHaveBeenCalled();
    } finally {
      vi.useRealTimers();
    }
  });

  it('still closes on a deliberate click on the backdrop', () => {
    vi.useFakeTimers({ toFake: ['Date'] });
    try {
      const onClose = vi.fn();
      const backdrop = renderOpen(onClose);
      vi.setSystemTime(Date.now() + 1000);
      fireEvent.click(backdrop);
      expect(onClose).toHaveBeenCalledTimes(1);
    } finally {
      vi.useRealTimers();
    }
  });
});
