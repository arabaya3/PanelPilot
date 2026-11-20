import { act, fireEvent, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { RevisionCompare } from '@/components/design/revision-compare';
import type { openProject, SavedProject } from '@/lib/design';

import { renderApp } from './helpers';

/** M18 panel: read-only, stale answers dropped, distinct states. SYNTHETIC data. */

const SUMMARIES = [1, 2, 3].map((number) => ({
  number,
  note: `note ${String(number)}`,
  author: number === 1 ? '' : 'Rana Haddad',
  author_unknown: number === 1,
  created_at: `2026-11-1${String(number)}T10:00:00Z`,
})) as unknown as SavedProject['revisions'];

function project(revision: number, over: Record<string, unknown> = {}): SavedProject {
  return {
    id: 'p1',
    name: 'Tower',
    revision,
    revisions: SUMMARIES,
    request: {
      info: {
        name: 'Tower',
        number: '',
        customer: `C${String(revision)}`,
        consultant: '',
        contractor: '',
        revisions: [],
      },
      boards: [
        {
          name: 'MDB',
          loads: [{ description: 'Lights', load: 'lighting', power_kw: String(revision) }],
        },
      ],
      feeder_links: [],
    },
    findings: [],
    validated: true,
    snapshot_sha256: `h${String(revision)}`,
    snapshot_schema: 'pp-design/3',
    configuration: {},
    configuration_status: 'stored',
    ...over,
  } as unknown as SavedProject;
}

type Open = typeof openProject;

function setup(open: Open) {
  const spy = vi.fn<Open>(open);
  const props = { token: 'tok', projectId: 'p1', revisions: SUMMARIES, impls: { open: spy } };
  const view = renderApp(<RevisionCompare {...props} />, { locale: 'en' });
  return { spy, props, view };
}

function answer(revision: number, over: Record<string, unknown> = {}) {
  return Promise.resolve({ kind: 'saved' as const, project: project(revision, over) });
}

describe('revision comparison panel', () => {
  it('compares the previous and latest revision only when asked, read-only', async () => {
    const { spy } = setup(({ revision }) => answer(revision ?? 3));
    expect(spy).not.toHaveBeenCalled();
    fireEvent.click(screen.getByTestId('compare-run'));
    await screen.findByTestId('compare-result');
    expect(spy.mock.calls.map(([options]) => options.revision).sort()).toEqual([2, 3]);
    expect(screen.getByTestId('compare-side-before').textContent).toMatch(
      /Before: revision 2.*Saved by Rana Haddad/,
    );
    expect(screen.getByTestId('compare-side-after').textContent).toMatch(/After: revision 3/);
    expect(screen.getByTestId('compare-status').textContent).toMatch(/Compared: 3 differences/); // details, board, stored design (hashes differ)
    // Power 2 kW → 3 kW, with its unit; customer C2 → C3.
    expect(screen.getByTestId('compare-section-boards').textContent).toMatch(/Power2 kW3 kW/);
    expect(screen.getByTestId('compare-section-details').textContent).toMatch(/CustomerC2C3/);
    // No action that changes anything is offered.
    for (const word of [/merge/i, /apply/i, /restore/i, /approve/i, /^save/i]) {
      expect(screen.queryByRole('button', { name: word })).toBeNull();
    }
  });

  it('keeps keyboard focus on Compare while it works and after', async () => {
    let finish: () => void = () => undefined;
    setup(
      ({ revision }) =>
        new Promise((resolve) => {
          finish = () => {
            resolve({ kind: 'saved', project: project(revision ?? 3) });
          };
        }),
    );
    const run = screen.getByTestId('compare-run');
    run.focus();
    fireEvent.click(run);
    expect(run.hasAttribute('disabled')).toBe(false);
    expect(run.getAttribute('aria-disabled')).toBe('true');
    expect(document.activeElement).toBe(run);
    await act(async () => {
      finish();
      await Promise.resolve();
    });
    expect(document.activeElement).toBe(screen.getByTestId('compare-run'));
  });

  it('leaving a choice mid-request and coming back never leaves it stuck busy', async () => {
    const pending: Array<() => void> = [];
    const { spy } = setup(
      ({ revision }) =>
        new Promise((resolve) => {
          pending.push(() => {
            resolve({ kind: 'saved', project: project(revision ?? 0) });
          });
        }),
    );
    fireEvent.click(screen.getByTestId('compare-run'));
    fireEvent.change(screen.getByTestId('compare-before'), { target: { value: '1' } });
    await act(async () => {
      for (const go of pending.splice(0)) go();
      await Promise.resolve();
    });
    fireEvent.change(screen.getByTestId('compare-before'), { target: { value: '2' } });
    expect(screen.getByTestId('compare-run').getAttribute('aria-disabled')).toBe('false');
    fireEvent.click(screen.getByTestId('compare-run'));
    expect(spy).toHaveBeenCalledTimes(4);
  });

  it('a request that throws says it failed and is not left busy', async () => {
    setup(() => Promise.reject(new Error('network')));
    fireEvent.click(screen.getByTestId('compare-run'));
    expect((await screen.findByTestId('compare-status')).textContent).toMatch(/could not be read/);
    expect(screen.getByTestId('compare-run').getAttribute('aria-disabled')).toBe('false');
  });

  it('says when the author was not recorded', async () => {
    setup(({ revision }) => answer(revision ?? 1));
    fireEvent.change(screen.getByTestId('compare-before'), { target: { value: '1' } });
    fireEvent.click(screen.getByTestId('compare-run'));
    expect((await screen.findByTestId('compare-side-before')).textContent).toMatch(
      /Author not recorded/,
    );
  });

  it('never shows an answer for choices changed while it was on its way', async () => {
    const pending: Array<() => void> = [];
    setup(
      ({ revision }) =>
        new Promise((resolve) => {
          pending.push(() => {
            resolve({ kind: 'saved', project: project(revision ?? 0) });
          });
        }),
    );
    fireEvent.click(screen.getByTestId('compare-run'));
    // Changed before the answer: that answer is for 2 → 3, not 1 → 3.
    fireEvent.change(screen.getByTestId('compare-before'), { target: { value: '1' } });
    await act(async () => {
      for (const go of pending) go();
      await Promise.resolve();
    });
    expect(screen.queryByTestId('compare-result')).toBeNull();
    // Not stuck busy for the new choice.
    expect(screen.getByTestId('compare-run').hasAttribute('disabled')).toBe(false);
  });

  it('a project or identity change drops what was compared', async () => {
    const { props, view } = setup(({ revision }) => answer(revision ?? 3));
    fireEvent.click(screen.getByTestId('compare-run'));
    await screen.findByTestId('compare-result');
    view.rerender(<RevisionCompare {...props} token="tok-other" />);
    await waitFor(() => {
      expect(screen.queryByTestId('compare-result')).toBeNull();
    });
  });

  it('an integrity failure shows that, and compares nothing', async () => {
    setup(({ revision }) =>
      answer(
        revision ?? 3,
        revision === 3
          ? {
              findings: [{ code: 'snapshot_integrity_failed', severity: 'blocking', message: 'x' }],
            }
          : {},
      ),
    );
    fireEvent.click(screen.getByTestId('compare-run'));
    expect((await screen.findByTestId('compare-integrity')).textContent).toMatch(
      /Revision 3 failed its integrity check/,
    );
    expect(screen.queryByTestId('compare-section-boards')).toBeNull();
  });

  it("a revision that is not the caller's shows nothing of either", async () => {
    setup(({ revision }) =>
      revision === 2
        ? Promise.resolve({
            kind: 'refused',
            detail: 'no such revision',
            code: 'revision_not_found',
          })
        : answer(revision ?? 3),
    );
    fireEvent.click(screen.getByTestId('compare-run'));
    expect((await screen.findByTestId('compare-status')).textContent).toMatch(
      /not available to you/,
    );
    expect(screen.queryByTestId('compare-result')).toBeNull();
  });

  it('an answer for another revision than asked is never shown as it', async () => {
    setup(() => answer(1));
    fireEvent.click(screen.getByTestId('compare-run'));
    expect((await screen.findByTestId('compare-status')).textContent).toMatch(/could not be read/);
    expect(screen.queryByTestId('compare-result')).toBeNull();
  });

  it('the same revision twice is not compared', () => {
    setup(({ revision }) => answer(revision ?? 3));
    fireEvent.change(screen.getByTestId('compare-before'), { target: { value: '3' } });
    expect(screen.getByTestId('compare-run').hasAttribute('disabled')).toBe(true);
    expect(screen.getByTestId('compare-status').textContent).toMatch(/two different revisions/);
  });

  it('unavailable data reads unavailable in the summary, not unchanged', async () => {
    setup(({ revision }) =>
      answer(
        revision ?? 3,
        revision === 2
          ? {
              snapshot_schema: 'pp-design/2',
              configuration_status: 'not_recoverable',
              configuration: null,
            }
          : {},
      ),
    );
    fireEvent.click(screen.getByTestId('compare-run'));
    const summary = await screen.findByTestId('compare-summary');
    expect(summary.textContent).toMatch(/Feeder links: Unavailable in revision 2/);
    expect(summary.textContent).toMatch(/Configuration and wiring: Unavailable in revision 2/);
  });

  it('each summary entry opens its section and moves focus to it, without navigating', async () => {
    setup(({ revision }) => answer(revision ?? 3));
    fireEvent.click(screen.getByTestId('compare-run'));
    const summary = await screen.findByTestId('compare-summary');
    expect(summary.querySelectorAll('a')).toHaveLength(0);
    const entries = Array.from(summary.querySelectorAll('button'));
    expect(entries).toHaveLength(5);
    for (const entry of entries) {
      const target = document.getElementById(
        entry.getAttribute('data-section') ?? '',
      ) as HTMLDetailsElement;
      expect(target).not.toBeNull();
      target.open = false;
      fireEvent.click(entry);
      expect(target.open).toBe(true);
      expect(document.activeElement).toBe(target.querySelector('summary'));
    }
  });
});
