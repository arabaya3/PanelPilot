import { fireEvent, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { HistorySidebar } from '@/components/chat/history-sidebar';
import type { listSessions, SessionListResult, SessionSummary } from '@/lib/sessions';

import { renderApp } from './helpers';

/**
 * Tests for the conversation history sidebar (FE-011).
 *
 * The spec's objective is that an engineer can "return to yesterday's session
 * without re-describing the problem", so the properties worth pinning are the
 * ones that break that: a list that silently loses a session, a row that
 * cannot be selected, and a failure that renders as an empty history rather
 * than as a failure. An empty sidebar and a broken sidebar look identical to
 * the person using one, and only one of them means "you have no history".
 */

function summary(overrides: Partial<SessionSummary> = {}): SessionSummary {
  return {
    id: 'session-1',
    title: 'ACS880 undervoltage trip',
    equipmentModel: null,
    turnCount: 2,
    createdAt: '2026-01-01T09:00:00Z',
    updatedAt: '2026-01-02T09:00:00Z',
    ...overrides,
  };
}

function listing(result: SessionListResult): typeof listSessions {
  return vi.fn().mockResolvedValue(result);
}

function loaded(sessions: SessionSummary[], nextCursor: string | null = null): SessionListResult {
  return { kind: 'loaded', sessions, nextCursor };
}

// --- the list ----------------------------------------------------------------

describe('the session list', () => {
  it('renders a row per conversation', async () => {
    renderApp(
      <HistorySidebar
        token="t"
        activeSessionId={null}
        onSelect={() => {}}
        listImpl={listing(
          loaded([summary({ id: 'a', title: 'first' }), summary({ id: 'b', title: 'second' })]),
        )}
      />,
    );

    expect(await screen.findByText('first')).not.toBeNull();
    expect(screen.getByText('second')).not.toBeNull();
  });

  it('preserves the order the API returned', async () => {
    // Not re-sorted client-side: the server knows when each conversation was
    // last added to, and this component only knows what it was sent. Sorting
    // here by `updatedAt` would look right and would silently disagree with
    // the cursor, which pages in the server's order.
    renderApp(
      <HistorySidebar
        token="t"
        activeSessionId={null}
        onSelect={() => {}}
        listImpl={listing(
          loaded([
            summary({ id: 'newest', title: 'newest', updatedAt: '2026-03-01T00:00:00Z' }),
            summary({ id: 'oldest', title: 'oldest', updatedAt: '2026-01-01T00:00:00Z' }),
          ]),
        )}
      />,
    );

    await screen.findByText('newest');
    const rendered = screen.getAllByRole('button').map((node) => node.textContent);
    expect(rendered[0]).toContain('newest');
    expect(rendered[1]).toContain('oldest');
  });

  it('shows the equipment model when the conversation identified one', async () => {
    // "showing brand/model per entry", from the spec.
    renderApp(
      <HistorySidebar
        token="t"
        activeSessionId={null}
        onSelect={() => {}}
        listImpl={listing(loaded([summary({ equipmentModel: 'ACS880' })]))}
      />,
    );

    expect(await screen.findByTestId('history-model-session-1')).not.toBeNull();
  });

  it('omits the model line when none was identified', async () => {
    // Rather than rendering an empty line, which on a dense list reads as a
    // value that failed to load.
    renderApp(
      <HistorySidebar
        token="t"
        activeSessionId={null}
        onSelect={() => {}}
        listImpl={listing(loaded([summary({ equipmentModel: null })]))}
      />,
    );

    await screen.findByText('ACS880 undervoltage trip');
    expect(screen.queryByTestId('history-model-session-1')).toBeNull();
  });

  it('marks the open conversation', async () => {
    renderApp(
      <HistorySidebar
        token="t"
        activeSessionId="session-1"
        onSelect={() => {}}
        listImpl={listing(loaded([summary()]))}
      />,
    );

    const row = await screen.findByTestId('history-item-session-1');
    expect(row.getAttribute('aria-current')).toBe('true');
  });

  it('does not mark a conversation that is not open', async () => {
    renderApp(
      <HistorySidebar
        token="t"
        activeSessionId="other"
        onSelect={() => {}}
        listImpl={listing(loaded([summary()]))}
      />,
    );

    const row = await screen.findByTestId('history-item-session-1');
    expect(row.getAttribute('aria-current')).toBeNull();
  });
});

// --- selection ---------------------------------------------------------------

describe('selecting a conversation', () => {
  it('reports the chosen session id', async () => {
    const onSelect = vi.fn();
    renderApp(
      <HistorySidebar
        token="t"
        activeSessionId={null}
        onSelect={onSelect}
        listImpl={listing(loaded([summary({ id: 'wanted' })]))}
      />,
    );

    fireEvent.click(await screen.findByTestId('history-item-wanted'));

    expect(onSelect).toHaveBeenCalledWith('wanted');
  });

  it('is a real button, so the list works without a mouse', async () => {
    // A clickable div would satisfy the click test above and be unreachable by
    // keyboard, which is the failure this pins.
    renderApp(
      <HistorySidebar
        token="t"
        activeSessionId={null}
        onSelect={() => {}}
        listImpl={listing(loaded([summary({ id: 'wanted' })]))}
      />,
    );

    const row = await screen.findByTestId('history-item-wanted');
    expect(row.tagName).toBe('BUTTON');
    expect(row.getAttribute('type')).toBe('button');
  });
});

// --- the states that must not look like "no history" -------------------------

describe('states other than a loaded list', () => {
  it('says so when the history could not be loaded', async () => {
    // The failure worth designing against: a failed fetch rendering as an
    // empty list tells the engineer their history is gone.
    renderApp(
      <HistorySidebar
        token="t"
        activeSessionId={null}
        onSelect={() => {}}
        listImpl={listing({ kind: 'failed' })}
      />,
    );

    expect(await screen.findByTestId('history-failed')).not.toBeNull();
    expect(screen.queryByTestId('history-empty')).toBeNull();
  });

  it('distinguishes an unavailable endpoint from a failure', async () => {
    // One is worth retrying and the other is not.
    renderApp(
      <HistorySidebar
        token="t"
        activeSessionId={null}
        onSelect={() => {}}
        listImpl={listing({ kind: 'unavailable' })}
      />,
    );

    expect(await screen.findByTestId('history-unavailable')).not.toBeNull();
  });

  it('says the history is empty only when it really is', async () => {
    renderApp(
      <HistorySidebar
        token="t"
        activeSessionId={null}
        onSelect={() => {}}
        listImpl={listing(loaded([]))}
      />,
    );

    expect(await screen.findByTestId('history-empty')).not.toBeNull();
  });
});

// --- pagination ---------------------------------------------------------------

describe('pagination', () => {
  it('offers more only when the API said there is more', async () => {
    renderApp(
      <HistorySidebar
        token="t"
        activeSessionId={null}
        onSelect={() => {}}
        listImpl={listing(loaded([summary()], null))}
      />,
    );

    await screen.findByText('ACS880 undervoltage trip');
    expect(screen.queryByTestId('history-load-more')).toBeNull();
  });

  it('appends the next page rather than replacing the list', async () => {
    // Replacing would make the sidebar appear to lose everything above the
    // fold the moment someone scrolled.
    const impl = vi
      .fn()
      .mockResolvedValueOnce(loaded([summary({ id: 'a', title: 'first' })], 'cursor-1'))
      .mockResolvedValueOnce(loaded([summary({ id: 'b', title: 'second' })], null));

    renderApp(
      <HistorySidebar token="t" activeSessionId={null} onSelect={() => {}} listImpl={impl} />,
    );

    fireEvent.click(await screen.findByTestId('history-load-more'));

    expect(await screen.findByText('second')).not.toBeNull();
    expect(screen.getByText('first')).not.toBeNull();
  });

  it('passes the cursor back unmodified', async () => {
    const impl = vi
      .fn()
      .mockResolvedValueOnce(loaded([summary({ id: 'a' })], 'opaque-cursor'))
      .mockResolvedValueOnce(loaded([], null));

    renderApp(
      <HistorySidebar token="t" activeSessionId={null} onSelect={() => {}} listImpl={impl} />,
    );

    fireEvent.click(await screen.findByTestId('history-load-more'));

    await waitFor(() => {
      expect(impl).toHaveBeenLastCalledWith(expect.objectContaining({ cursor: 'opaque-cursor' }));
    });
  });

  it('does not render the same session twice across pages', async () => {
    // A session that gained a turn between two fetches can legitimately come
    // back on both. Rendering it twice reads as duplicated history.
    const impl = vi
      .fn()
      .mockResolvedValueOnce(loaded([summary({ id: 'a', title: 'repeated' })], 'c1'))
      .mockResolvedValueOnce(loaded([summary({ id: 'a', title: 'repeated' })], null));

    renderApp(
      <HistorySidebar token="t" activeSessionId={null} onSelect={() => {}} listImpl={impl} />,
    );

    fireEvent.click(await screen.findByTestId('history-load-more'));

    await waitFor(() => {
      expect(screen.queryByTestId('history-load-more')).toBeNull();
    });
    expect(screen.getAllByText('repeated')).toHaveLength(1);
  });

  it('stops offering more at the end of the list', async () => {
    const impl = vi
      .fn()
      .mockResolvedValueOnce(loaded([summary({ id: 'a' })], 'c1'))
      .mockResolvedValueOnce(loaded([summary({ id: 'b' })], null));

    renderApp(
      <HistorySidebar token="t" activeSessionId={null} onSelect={() => {}} listImpl={impl} />,
    );

    fireEvent.click(await screen.findByTestId('history-load-more'));

    await waitFor(() => {
      expect(screen.queryByTestId('history-load-more')).toBeNull();
    });
  });
});

// --- layout ------------------------------------------------------------------

describe('layout', () => {
  it('uses logical inline-end for its separator, not a physical side', async () => {
    // The spec's RTL requirement: the sidebar must land on the visual right in
    // Arabic and Hebrew from the same markup. `border-r` would draw a rule
    // down the middle of the page in those locales.
    renderApp(
      <HistorySidebar
        token="t"
        activeSessionId={null}
        onSelect={() => {}}
        listImpl={listing(loaded([summary()]))}
      />,
    );

    const nav = await screen.findByTestId('history-sidebar');
    expect(nav.className).toContain('border-e');
    expect(nav.className).not.toContain('border-r');
    expect(nav.className).not.toContain('border-l');
  });

  it('labels itself for assistive technology', async () => {
    renderApp(
      <HistorySidebar
        token="t"
        activeSessionId={null}
        onSelect={() => {}}
        listImpl={listing(loaded([summary()]))}
      />,
    );

    expect(await screen.findByRole('navigation', { name: /history/i })).not.toBeNull();
  });
});
