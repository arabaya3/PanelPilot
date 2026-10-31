import { screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { ProjectsPanel } from '@/components/design/projects-panel';
import type { listProjects } from '@/lib/design';

import { renderApp } from './helpers';

/**
 * Reloading a few tabs spent the design budget for the address (240 requests
 * in 5 minutes): the panel listed projects again on every render, because the
 * page hands it a new `onUnauthorized` each time, and a 429 answer was one
 * more render, so one more request.
 */

const RATE_LIMITED = {
  kind: 'refused' as const,
  detail: 'too many design requests',
  code: 'rate_limited',
  params: {},
  retryAfterSeconds: 120,
};

afterEach(() => {
  vi.restoreAllMocks();
});

function panel(token: string, list: typeof listProjects) {
  return (
    <ProjectsPanel
      token={token}
      current={null}
      opened={null}
      onOpened={vi.fn()}
      // A new function on every render, as the design page passes it.
      onUnauthorized={() => undefined}
      impls={{ list }}
    />
  );
}

describe('listing saved projects', () => {
  it('lists once per identity, however often the page re-renders', async () => {
    const list = vi
      .fn<typeof listProjects>()
      .mockResolvedValue({ kind: 'listed', page: { projects: [], next_cursor: null } });
    const { rerender } = renderApp(panel('tok', list));
    await waitFor(() => {
      expect(list).toHaveBeenCalledTimes(1);
    });
    for (let n = 0; n < 5; n += 1) rerender(panel('tok', list));
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(list).toHaveBeenCalledTimes(1);
  });

  it('does not retry a 429: it says so, and waits out Retry-After even across a new token', async () => {
    const now = vi.spyOn(Date, 'now').mockReturnValue(1_000_000);
    const list = vi.fn<typeof listProjects>().mockResolvedValue(RATE_LIMITED);
    const { rerender } = renderApp(panel('tok', list));
    expect((await screen.findByTestId('projects-message')).textContent).toBe(
      'Too many design requests from this network; wait a few minutes.',
    );
    for (let n = 0; n < 5; n += 1) rerender(panel('tok', list));
    // A renewed token within the wait asks for nothing either.
    rerender(panel('tok-2', list));
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(list).toHaveBeenCalledTimes(1);

    // Once Retry-After has passed, the next identity change lists again.
    now.mockReturnValue(1_000_000 + 121_000);
    list.mockResolvedValue({ kind: 'listed', page: { projects: [], next_cursor: null } });
    rerender(panel('tok-3', list));
    await waitFor(() => {
      expect(list).toHaveBeenCalledTimes(2);
    });
  });
});
