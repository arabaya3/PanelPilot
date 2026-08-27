import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import {
  ClaimConflict,
  VerificationConsole,
  type VerificationApi,
  type VerificationLabels,
} from '@/components/verification';
import { canSubmit, requiresNote } from '@/components/verification/labeller';

/**
 * Tests for the verification console.
 *
 * Two things the task asks for by name: a full click-through of one
 * verification cycle, and a check that the mandatory-note requirement actually
 * blocks submission when empty. Plus the stated edge case — a claim race must
 * show "already claimed by X" rather than allowing a silent double-submit.
 *
 * The note rule is tested twice deliberately: once as a pure function, once
 * through the UI. The function is the rule; the UI is one way of enforcing it,
 * and a rule that holds only in the component is one a second submit path
 * would quietly bypass.
 */

const LABELS: VerificationLabels = {
  heading: 'Verification queue',
  empty: 'Nothing left to review.',
  itemCount: '{count} remaining',
  proposed: 'Proposed content',
  source: 'Source document',
  sourceMissing: 'No source available for this item.',
  correct: 'Correct',
  incorrect: 'Incorrect',
  uncertain: 'Uncertain',
  notePlaceholder: 'Say what is wrong, and where.',
  noteRequired: 'A note is required',
  submit: 'Submit',
  submitting: 'Submitting…',
  claimedBy: 'Already claimed by {name}',
  submitFailed: 'Could not submit. Try again.',
};

function items(count = 2) {
  return Array.from({ length: count }, (_, index) => ({
    id: `item-${String(index)}`,
    chunk_id: `chunk-${String(index)}`,
    status: 'pending',
    assigned_at: '2026-06-01T12:00:00Z',
  }));
}

function api(overrides: Partial<VerificationApi> = {}): VerificationApi {
  return {
    submitLabel: vi.fn().mockResolvedValue(undefined),
    ...overrides,
  };
}

// --- the note rule, as a rule -------------------------------------------------

describe('the mandatory note rule', () => {
  it('requires a note for incorrect and uncertain', () => {
    expect(requiresNote('incorrect')).toBe(true);
    expect(requiresNote('uncertain')).toBe(true);
  });

  it('does not require one for correct', () => {
    // A note on every item would be friction on the common case, and friction
    // on the common case is what makes people click through on autopilot.
    expect(requiresNote('correct')).toBe(false);
  });

  it('blocks submission when an escalating label has no note', () => {
    expect(canSubmit('incorrect', '')).toBe(false);
    expect(canSubmit('uncertain', '')).toBe(false);
  });

  it('treats whitespace as no note at all', () => {
    // A space satisfies `required` in HTML and tells a lead nothing.
    expect(canSubmit('incorrect', '   \n  ')).toBe(false);
  });

  it('allows submission once a real note is present', () => {
    expect(canSubmit('incorrect', 'cited section gives 63 A')).toBe(true);
  });

  it('blocks submission when no label is chosen at all', () => {
    expect(canSubmit(null, 'a note')).toBe(false);
  });
});

// --- the full cycle -----------------------------------------------------------

describe('one verification cycle', () => {
  it('shows the proposal and the source side by side', () => {
    // The acceptance criterion's "without leaving the tool". A verifier who
    // opens the manual in another tab is checking against memory, which is not
    // verification.
    render(
      <VerificationConsole
        items={items(1)}
        api={api()}
        sourceUrlFor={() => 'https://example.invalid/manual.pdf#page=27'}
        labels={LABELS}
      />,
    );

    expect(screen.getByTestId('chunk-id').textContent).toBe('chunk-0');
    expect(screen.getByTestId('source-frame').getAttribute('src')).toContain('page=27');
  });

  it('completes a correct label without a note', () => {
    const submitLabel = vi.fn().mockResolvedValue(undefined);
    render(
      <VerificationConsole
        items={items(1)}
        api={api({ submitLabel })}
        sourceUrlFor={() => 'https://example.invalid/doc'}
        labels={LABELS}
      />,
    );

    fireEvent.click(screen.getByTestId('label-correct'));
    fireEvent.click(screen.getByTestId('submit'));

    expect(submitLabel).toHaveBeenCalledWith('item-0', 'correct', '');
  });

  it('advances to the next item after a submission', async () => {
    render(
      <VerificationConsole
        items={items(2)}
        api={api()}
        sourceUrlFor={() => 'https://example.invalid/doc'}
        labels={LABELS}
      />,
    );

    expect(screen.getByTestId('chunk-id').textContent).toBe('chunk-0');

    fireEvent.click(screen.getByTestId('label-correct'));
    fireEvent.click(screen.getByTestId('submit'));

    await waitFor(() => {
      expect(screen.getByTestId('chunk-id').textContent).toBe('chunk-1');
    });
  });

  it('reports how many items are left', () => {
    render(
      <VerificationConsole
        items={items(3)}
        api={api()}
        sourceUrlFor={() => 'https://example.invalid/doc'}
        labels={LABELS}
      />,
    );

    expect(screen.getByTestId('remaining').textContent).toBe('3 remaining');
  });

  it('says so when the queue is finished', async () => {
    render(
      <VerificationConsole
        items={items(1)}
        api={api()}
        sourceUrlFor={() => 'https://example.invalid/doc'}
        labels={LABELS}
      />,
    );

    fireEvent.click(screen.getByTestId('label-correct'));
    fireEvent.click(screen.getByTestId('submit'));

    await waitFor(() => {
      expect(screen.getByTestId('queue-empty')).not.toBeNull();
    });
  });
});

// --- the note requirement, through the UI -------------------------------------

describe('the note requirement in the interface', () => {
  it('disables submit for incorrect until a note is typed', () => {
    render(
      <VerificationConsole
        items={items(1)}
        api={api()}
        sourceUrlFor={() => 'https://example.invalid/doc'}
        labels={LABELS}
      />,
    );

    fireEvent.click(screen.getByTestId('label-incorrect'));

    expect(screen.getByTestId('submit').hasAttribute('disabled')).toBe(true);
  });

  it('does not call the API while the note is empty', () => {
    // The disabled attribute is a hint; this is the check that matters. A
    // click that reached the API would submit an unexplained escalation.
    const submitLabel = vi.fn().mockResolvedValue(undefined);
    render(
      <VerificationConsole
        items={items(1)}
        api={api({ submitLabel })}
        sourceUrlFor={() => 'https://example.invalid/doc'}
        labels={LABELS}
      />,
    );

    fireEvent.click(screen.getByTestId('label-uncertain'));
    fireEvent.click(screen.getByTestId('submit'));

    expect(submitLabel).not.toHaveBeenCalled();
  });

  it('enables submit once a note is typed', () => {
    render(
      <VerificationConsole
        items={items(1)}
        api={api()}
        sourceUrlFor={() => 'https://example.invalid/doc'}
        labels={LABELS}
      />,
    );

    fireEvent.click(screen.getByTestId('label-incorrect'));
    fireEvent.change(screen.getByTestId('note'), {
      target: { value: 'cited section gives 63 A, chunk says 80 A' },
    });

    expect(screen.getByTestId('submit').hasAttribute('disabled')).toBe(false);
  });

  it('sends the note with the label', () => {
    const submitLabel = vi.fn().mockResolvedValue(undefined);
    render(
      <VerificationConsole
        items={items(1)}
        api={api({ submitLabel })}
        sourceUrlFor={() => 'https://example.invalid/doc'}
        labels={LABELS}
      />,
    );

    fireEvent.click(screen.getByTestId('label-incorrect'));
    fireEvent.change(screen.getByTestId('note'), { target: { value: 'wrong ambient' } });
    fireEvent.click(screen.getByTestId('submit'));

    expect(submitLabel).toHaveBeenCalledWith('item-0', 'incorrect', 'wrong ambient');
  });

  it('shows no note field for a correct label', () => {
    render(
      <VerificationConsole
        items={items(1)}
        api={api()}
        sourceUrlFor={() => 'https://example.invalid/doc'}
        labels={LABELS}
      />,
    );

    fireEvent.click(screen.getByTestId('label-correct'));

    expect(screen.queryByTestId('note')).toBeNull();
  });
});

// --- the claim race -----------------------------------------------------------

describe('a claim race', () => {
  it('says who holds the item rather than allowing a silent double-submit', async () => {
    // The stated edge case. Two verifiers can open the same item, and the
    // loser must be told — a silent failure would leave them believing they
    // had reviewed it.
    const submitLabel = vi.fn().mockRejectedValue(new ClaimConflict('Dana'));
    render(
      <VerificationConsole
        items={items(1)}
        api={api({ submitLabel })}
        sourceUrlFor={() => 'https://example.invalid/doc'}
        labels={LABELS}
      />,
    );

    fireEvent.click(screen.getByTestId('label-correct'));
    fireEvent.click(screen.getByTestId('submit'));

    await waitFor(() => {
      expect(screen.getByTestId('queue-error').textContent).toBe('Already claimed by Dana');
    });
  });

  it('removes a lost item rather than inviting a retry that will fail', async () => {
    const submitLabel = vi.fn().mockRejectedValue(new ClaimConflict('Dana'));
    render(
      <VerificationConsole
        items={items(2)}
        api={api({ submitLabel })}
        sourceUrlFor={() => 'https://example.invalid/doc'}
        labels={LABELS}
      />,
    );

    fireEvent.click(screen.getByTestId('label-correct'));
    fireEvent.click(screen.getByTestId('submit'));

    await waitFor(() => {
      expect(screen.getByTestId('chunk-id').textContent).toBe('chunk-1');
    });
  });

  it('distinguishes a lost race from a failed request', async () => {
    // Different causes, different next steps: one means move on, the other
    // means try again. Collapsing them would tell a verifier to retry
    // something that will never succeed, or to abandon something that would.
    const submitLabel = vi.fn().mockRejectedValue(new Error('network down'));
    render(
      <VerificationConsole
        items={items(1)}
        api={api({ submitLabel })}
        sourceUrlFor={() => 'https://example.invalid/doc'}
        labels={LABELS}
      />,
    );

    fireEvent.click(screen.getByTestId('label-correct'));
    fireEvent.click(screen.getByTestId('submit'));

    await waitFor(() => {
      expect(screen.getByTestId('submit-error').textContent).toBe('Could not submit. Try again.');
    });
  });

  it('keeps the item after a failed request so it can be retried', async () => {
    const submitLabel = vi.fn().mockRejectedValue(new Error('network down'));
    render(
      <VerificationConsole
        items={items(2)}
        api={api({ submitLabel })}
        sourceUrlFor={() => 'https://example.invalid/doc'}
        labels={LABELS}
      />,
    );

    fireEvent.click(screen.getByTestId('label-correct'));
    fireEvent.click(screen.getByTestId('submit'));

    await waitFor(() => {
      expect(screen.getByTestId('submit-error')).not.toBeNull();
    });
    expect(screen.getByTestId('chunk-id').textContent).toBe('chunk-0');
  });
});

// --- the source pane ----------------------------------------------------------

describe('the source pane', () => {
  it('says so when an item has no source rather than showing a blank frame', () => {
    // A verifier facing a blank pane cannot tell whether the source failed to
    // load or the item has none — and one of those means they must not label
    // it `correct`.
    render(
      <VerificationConsole
        items={items(1)}
        api={api()}
        sourceUrlFor={() => null}
        labels={LABELS}
      />,
    );

    expect(screen.getByTestId('source-missing')).not.toBeNull();
    expect(screen.queryByTestId('source-frame')).toBeNull();
  });

  it('passes the citation fragment through to the viewer', () => {
    // "page/section pre-highlighted" — the verifier should land on the cited
    // page, not on page one of a 400-page manual.
    render(
      <VerificationConsole
        items={items(1)}
        api={api()}
        sourceUrlFor={() => 'https://example.invalid/manual.pdf#page=41&section=3.4'}
        labels={LABELS}
      />,
    );

    const src = screen.getByTestId('source-frame').getAttribute('src') ?? '';
    expect(src).toContain('#page=41');
    expect(src).toContain('section=3.4');
  });
});
