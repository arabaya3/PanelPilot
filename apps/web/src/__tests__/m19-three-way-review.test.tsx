import { act, fireEvent, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { ThreeWayReview, type Captured } from '@/components/design/three-way-review';
import type { openProject, SavedProject } from '@/lib/design';

import { renderApp } from './helpers';

/** M19 review panel: read-only, labelled sources, stale answers dropped. SYNTHETIC data. */

const loads = (kw: string) => [
  { description: 'Lights', load: 'lighting', power_kw: kw, phases: 1, length_m: '12' },
];
const request = (customer: string, kw: string) => ({
  info: { name: 'Tower', number: '', customer, consultant: '', contractor: '', revisions: [] },
  boards: [
    {
      name: 'MDB',
      supply: { voltage_v: '400', phases: 3, fault_level_ka: '10' },
      loads: loads(kw),
    },
  ],
  profile: null,
});

function project(
  revision: number,
  customer: string,
  kw: string,
  over: Record<string, unknown> = {},
): SavedProject {
  return {
    id: 'p1',
    name: 'Tower',
    revision,
    revisions: [1, 2, 3].map((n) => ({
      number: n,
      note: '',
      author: 'Rana',
      author_unknown: false,
      created_at: '2026-11-19T10:00:00Z',
    })),
    request: request(customer, kw),
    findings: [],
    snapshot_schema: 'pp-design/3',
    configuration: { templates: [], patterns: [], strips: [], components: [], bindings: [] },
    configuration_status: 'stored',
    ...over,
  } as unknown as SavedProject;
}

/** Your work: base revision 2's request with Lights at 2.2 kW (as the editor sends it). */
function yours(mark = 'm1'): Captured {
  return {
    request: {
      info: { name: 'Tower', number: '', customer: 'C2', consultant: '', contractor: '' },
      boards: [
        {
          name: 'MDB',
          location: null,
          fed_from: null,
          feeder_length_m: null,
          supply: {
            voltage_v: '400',
            phases: 3,
            frequency_hz: '50',
            earthing: 'TN-S',
            fault_level_ka: '10',
            earth_loop_ohm: null,
          },
          loads: [
            {
              description: 'Lights',
              load: 'lighting',
              power_kw: '2.2',
              phases: 1,
              power_factor: null,
              controlled: false,
              starter: null,
              length_m: '12',
            },
          ],
        },
      ],
      profile: null,
    },
    configuration: { templates: [], patterns: [], strips: [], components: [], bindings: [] },
    links: true,
    mark,
  };
}

type Open = typeof openProject;
const ok = (p: SavedProject) => Promise.resolve({ kind: 'saved' as const, project: p });

function setup(open: Open, props: Partial<Parameters<typeof ThreeWayReview>[0]> = {}) {
  const spy = vi.fn<Open>(open);
  const capture = vi.fn(() => yours());
  const onClose = vi.fn();
  const all = {
    token: 'tok',
    projectId: 'p1',
    base: 2 as number | null,
    capture,
    workMark: 'm1',
    onClose,
    impls: { open: spy },
    ...props,
  };
  const view = renderApp(<ThreeWayReview {...all} />, { locale: 'en' });
  return { spy, capture, onClose, all, view };
}

// Latest = revision 3 with a customer change (C3) and Lights unchanged.
const standard: Open = ({ revision }) =>
  revision === 2 ? ok(project(2, 'C2', '1.5')) : ok(project(3, 'C3', '1.5'));

describe('three-way review panel', () => {
  it('fetches the base by number and the latest without one, and labels all three sources', async () => {
    const { spy, capture } = setup(standard);
    await screen.findByTestId('review-result');
    expect(spy.mock.calls.map(([o]) => o.revision)).toEqual([2, null]);
    expect(capture).toHaveBeenCalledTimes(1);
    expect(screen.getByTestId('review-source-base').textContent).toMatch(/Base: revision 2/);
    expect(screen.getByTestId('review-source-yours').textContent).toMatch(
      /Your unsaved work.*Captured/,
    );
    expect(screen.getByTestId('review-source-latest').textContent).toMatch(
      /Latest saved revision when fetched: 3.*Another revision may have been saved since/,
    );
    // Lights 1.5 → 2.2 only in yours; customer C2 → C3 only in latest.
    const rows = screen
      .getAllByTestId('review-row')
      .map((r) => [r.getAttribute('data-status'), r.textContent]);
    expect(rows.some(([s, text]) => s === 'yours' && /Lights/.test(text ?? ''))).toBe(true);
    expect(rows.some(([s, text]) => s === 'latest' && /Customer/.test(text ?? ''))).toBe(true);
  });

  it('offers no action that changes anything', async () => {
    setup(standard);
    await screen.findByTestId('review-result');
    for (const word of [
      /apply/i,
      /merge/i,
      /keep mine/i,
      /take theirs/i,
      /^save/i,
      /approve/i,
      /restore/i,
    ]) {
      expect(screen.queryByRole('button', { name: word })).toBeNull();
    }
  });

  it('with no known base it explains, fetches nothing, and never uses the latest instead', async () => {
    const { spy } = setup(standard, { base: null, madeFrom: 7 });
    expect((await screen.findByTestId('review-no-base')).textContent).toMatch(
      /made from revision 7.*never replaced by the latest revision/,
    );
    expect(spy).not.toHaveBeenCalled();
    expect(screen.queryByTestId('review-result')).toBeNull();
  });

  it('a base failing its integrity check cannot be a base', async () => {
    setup(({ revision }) =>
      revision === 2
        ? ok(
            project(2, 'C2', '1.5', {
              findings: [{ code: 'snapshot_integrity_failed', severity: 'blocking', message: 'x' }],
            }),
          )
        : ok(project(3, 'C3', '1.5')),
    );
    await waitFor(() => {
      expect(screen.getByTestId('review-status').textContent).toMatch(
        /Base revision 2 failed its integrity check/,
      );
    });
    expect(screen.queryByTestId('review-result')).toBeNull();
  });

  it('a latest revision failing its integrity check is said, and nothing is compared', async () => {
    setup(({ revision }) =>
      revision === 2
        ? ok(project(2, 'C2', '1.5'))
        : ok(
            project(3, 'C3', '1.5', {
              findings: [{ code: 'snapshot_schema_unknown', severity: 'blocking', message: 'x' }],
            }),
          ),
    );
    await waitFor(() => {
      expect(screen.getByTestId('review-status').textContent).toMatch(
        /latest revision \(3\) failed its integrity check/,
      );
    });
    expect(screen.queryByTestId('review-result')).toBeNull();
  });

  it("a base that is not the caller's shows nothing", async () => {
    setup(({ revision }) =>
      revision === 2
        ? Promise.resolve({ kind: 'refused', detail: 'no', code: 'revision_not_found' })
        : ok(project(3, 'C3', '1.5')),
    );
    await waitFor(() => {
      expect(screen.getByTestId('review-status').textContent).toMatch(
        /Base revision 2 is not available to you/,
      );
    });
    expect(screen.queryByTestId('review-result')).toBeNull();
  });

  it('marks the captured copy outdated when the editor changes, and Refresh captures again', async () => {
    const { all, view, capture } = setup(standard);
    await screen.findByTestId('review-result');
    expect(screen.queryByTestId('review-outdated')).toBeNull();
    view.rerender(<ThreeWayReview {...all} workMark="m2" />);
    expect(screen.getByTestId('review-outdated').textContent).toMatch(
      /changed after this review was captured/,
    );
    capture.mockImplementation(() => yours('m2'));
    fireEvent.click(screen.getByTestId('review-refresh'));
    await waitFor(() => {
      expect(capture).toHaveBeenCalledTimes(2);
    });
    await waitFor(() => {
      expect(screen.queryByTestId('review-outdated')).toBeNull();
    });
  });

  it('a slow first answer never overwrites a newer round', async () => {
    const answers: Array<() => void> = [];
    let round = 0;
    setup(({ revision }) => {
      const mine = round;
      return new Promise((resolve) => {
        answers.push(() => {
          resolve({
            kind: 'saved',
            project:
              revision === 2
                ? project(2, 'C2', '1.5')
                : project(3, mine === 0 ? 'OLD' : 'NEW', '1.5'),
          });
        });
      });
    });
    await waitFor(() => {
      expect(answers).toHaveLength(2);
    });
    round = 1;
    fireEvent.click(screen.getByTestId('review-refresh'));
    await waitFor(() => {
      expect(answers).toHaveLength(4);
    });
    // The newer round answers first, then the old one arrives late.
    await act(async () => {
      answers[2]?.();
      answers[3]?.();
      await Promise.resolve();
    });
    await act(async () => {
      answers[0]?.();
      answers[1]?.();
      await Promise.resolve();
    });
    const text = (await screen.findByTestId('review-result')).textContent;
    expect(text).toMatch(/NEW/);
    expect(text).not.toMatch(/OLD/);
  });

  it('an identity change while fetching drops the answer for the old identity', async () => {
    const resolvers: Array<() => void> = [];
    const { all, view } = setup(
      ({ revision }) =>
        new Promise((resolve) => {
          resolvers.push(() => {
            resolve({
              kind: 'saved',
              project: revision === 2 ? project(2, 'C2', '1.5') : project(3, 'C3', '1.5'),
            });
          });
        }),
    );
    await waitFor(() => {
      expect(resolvers).toHaveLength(2);
    });
    view.rerender(<ThreeWayReview {...all} token="other" />);
    // Only the old identity's two requests answer.
    await act(async () => {
      resolvers[0]?.();
      resolvers[1]?.();
      await Promise.resolve();
    });
    expect(screen.queryByTestId('review-result')).toBeNull();
    expect(screen.getByTestId('review-status').textContent).toMatch(/Fetching/);
  });

  it('an answer that arrives after your work changed is not shown', async () => {
    const resolvers: Array<() => void> = [];
    const { all, view } = setup(
      ({ revision }) =>
        new Promise((resolve) => {
          resolvers.push(() => {
            resolve({
              kind: 'saved',
              project: revision === 2 ? project(2, 'C2', '1.5') : project(3, 'C3', '1.5'),
            });
          });
        }),
    );
    await waitFor(() => {
      expect(resolvers).toHaveLength(2);
    });
    view.rerender(<ThreeWayReview {...all} workMark="m-edited" />);
    await act(async () => {
      for (const go of resolvers) go();
      await Promise.resolve();
    });
    await waitFor(() => {
      expect(screen.getByTestId('review-status').textContent).toMatch(
        /changed while the revisions were fetched/,
      );
    });
    expect(screen.queryByTestId('review-result')).toBeNull();
  });

  describe('session renewal (SIMULATED delayed 401 answers)', () => {
    function delayed() {
      const resolvers: Array<(answer: Awaited<ReturnType<Open>>) => void> = [];
      const open: Open = () =>
        new Promise((resolve) => {
          resolvers.push(resolve);
        });
      return { resolvers, open };
    }
    const unauthorized = { kind: 'unauthorized' as const };

    it('a 401 that arrives after you edited asks for renewal once, compares nothing, and does not re-run', async () => {
      const { resolvers, open } = delayed();
      const onUnauthorized = vi.fn();
      const { all, view, capture, spy } = setup(open, { onUnauthorized });
      await waitFor(() => {
        expect(resolvers).toHaveLength(2);
      });
      view.rerender(
        <ThreeWayReview {...all} onUnauthorized={onUnauthorized} workMark="m-edited" />,
      );
      await act(async () => {
        for (const go of resolvers) go(unauthorized);
        await Promise.resolve();
      });
      expect(onUnauthorized).toHaveBeenCalledTimes(1);
      await waitFor(() => {
        expect(screen.getByTestId('review-status').textContent).toMatch(/could not be read/);
      });
      expect(screen.queryByTestId('review-result')).toBeNull();
      // No retry loop: nothing more is asked for until the engineer refreshes (or the token changes).
      await new Promise((resolve) => setTimeout(resolve, 50));
      expect(spy).toHaveBeenCalledTimes(2);
      expect(capture).toHaveBeenCalledTimes(1);
    });

    it('an answer for a review that was closed or remounted asks for no renewal', async () => {
      const { resolvers, open } = delayed();
      const onUnauthorized = vi.fn();
      const { view } = setup(open, { onUnauthorized });
      await waitFor(() => {
        expect(resolvers).toHaveLength(2);
      });
      view.unmount();
      await act(async () => {
        for (const go of resolvers) go(unauthorized);
        await Promise.resolve();
      });
      expect(onUnauthorized).not.toHaveBeenCalled();
    });

    it('a 401 for the identity before (the token changed meanwhile) asks for no renewal and shows nothing of it', async () => {
      const { resolvers, open } = delayed();
      const onUnauthorized = vi.fn();
      const { all, view } = setup(open, { onUnauthorized });
      await waitFor(() => {
        expect(resolvers).toHaveLength(2);
      });
      view.rerender(
        <ThreeWayReview {...all} onUnauthorized={onUnauthorized} token="tok-renewed" />,
      );
      await act(async () => {
        resolvers[0]?.(unauthorized);
        resolvers[1]?.(unauthorized);
        await Promise.resolve();
      });
      expect(onUnauthorized).not.toHaveBeenCalled();
      // The renewed token's own round runs and its answer is shown.
      await waitFor(() => {
        expect(resolvers).toHaveLength(4);
      });
      await act(async () => {
        resolvers[2]?.({ kind: 'saved', project: project(2, 'C2', '1.5') });
        resolvers[3]?.({ kind: 'saved', project: project(3, 'C3', '1.5') });
        await Promise.resolve();
      });
      expect(await screen.findByTestId('review-result')).toBeTruthy();
    });

    it('a renewed token that is refused again does not renew again (no loop)', async () => {
      const { resolvers, open } = delayed();
      const onUnauthorized = vi.fn();
      const { all, view } = setup(open, { onUnauthorized });
      await waitFor(() => {
        expect(resolvers).toHaveLength(2);
      });
      await act(async () => {
        for (const go of resolvers.splice(0)) go(unauthorized);
        await Promise.resolve();
      });
      expect(onUnauthorized).toHaveBeenCalledTimes(1);
      // The renewal hands a new token; the review re-runs once with it and is refused again.
      view.rerender(<ThreeWayReview {...all} onUnauthorized={onUnauthorized} token="tok-2" />);
      await waitFor(() => {
        expect(resolvers).toHaveLength(2);
      });
      await act(async () => {
        for (const go of resolvers.splice(0)) go(unauthorized);
        await Promise.resolve();
      });
      expect(onUnauthorized).toHaveBeenCalledTimes(1);
      await waitFor(() => {
        expect(screen.getByTestId('review-status').textContent).toMatch(/could not be read/);
      });
      await new Promise((resolve) => setTimeout(resolve, 50));
      expect(resolvers).toHaveLength(0);
    });

    it('after a renewal, a new base in the same round that is refused asks again (once)', async () => {
      const { resolvers, open } = delayed();
      const onUnauthorized = vi.fn();
      const { all, view } = setup(open, { onUnauthorized });
      await waitFor(() => {
        expect(resolvers).toHaveLength(2);
      });
      await act(async () => {
        for (const go of resolvers.splice(0)) go(unauthorized);
        await Promise.resolve();
      });
      expect(onUnauthorized).toHaveBeenCalledTimes(1);
      // Renewed; later the editing base moves (a save) and that run is refused.
      view.rerender(<ThreeWayReview {...all} onUnauthorized={onUnauthorized} token="tok-2" base={3} />);
      await waitFor(() => {
        expect(resolvers).toHaveLength(2);
      });
      await act(async () => {
        for (const go of resolvers.splice(0)) go(unauthorized);
        await Promise.resolve();
      });
      expect(onUnauthorized).toHaveBeenCalledTimes(2);
    });

    it('a renewal that failed (token unchanged) leaves a recoverable state: Refresh asks again, once', async () => {
      const { resolvers, open } = delayed();
      const onUnauthorized = vi.fn();
      setup(open, { onUnauthorized });
      await waitFor(() => {
        expect(resolvers).toHaveLength(2);
      });
      await act(async () => {
        for (const go of resolvers.splice(0)) go(unauthorized);
        await Promise.resolve();
      });
      expect(onUnauthorized).toHaveBeenCalledTimes(1);
      fireEvent.click(screen.getByTestId('review-refresh'));
      await waitFor(() => {
        expect(resolvers).toHaveLength(2);
      });
      await act(async () => {
        for (const go of resolvers.splice(0)) go(unauthorized);
        await Promise.resolve();
      });
      expect(onUnauthorized).toHaveBeenCalledTimes(2);
    });
  });

  it('when no newer revision exists, says so and shows only your changes', async () => {
    setup(({ revision }) => ok(project(2, 'C2', '1.5', revision === null ? {} : {})));
    expect((await screen.findByTestId('review-no-newer')).textContent).toMatch(
      /No revision newer than 2/,
    );
    expect(
      screen.getAllByTestId('review-row').every((r) => r.getAttribute('data-status') === 'yours'),
    ).toBe(true);
  });

  it('moves focus to its heading when opened, and Close is a plain action', async () => {
    const { onClose } = setup(standard);
    await screen.findByTestId('review-result');
    expect(document.activeElement?.tagName).toBe('H3');
    fireEvent.click(screen.getByTestId('review-close'));
    expect(onClose).toHaveBeenCalled();
  });
});
