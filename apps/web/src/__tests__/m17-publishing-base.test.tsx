import { act, fireEvent, screen, waitFor } from '@testing-library/react';
import { useRef, useState } from 'react';
import { describe, expect, it, vi } from 'vitest';

import { ProjectsPanel, type OpenedProject } from '@/components/design/projects-panel';
import { Dialog } from '@/components/ui';
import type { listProjects, ProjectDesignRequest, reviseProject, SavedProject } from '@/lib/design';

import { renderApp } from './helpers';

/**
 * The publishing base: a save states the revision its work was made from,
 * never the newest revision merely seen since. SYNTHETIC data.
 */

const REQUEST = {
  info: { name: 'Tower' },
  boards: [{ name: 'MDB', loads: [{ description: 'Lights', load: 'lighting', power_kw: '1' }] }],
} as unknown as ProjectDesignRequest;

function opened(revision: number, revisions: number[]): OpenedProject {
  return {
    id: 'p1',
    name: 'Tower',
    revision,
    revisions,
    approval: null,
    approvingAs: null,
    canApprove: false,
  };
}

const STALE = {
  kind: 'refused' as const,
  detail: 'stale',
  code: 'revision_stale_base',
  params: { expected: '2', latest: '3' },
};

function setup(
  options: {
    open?: OpenedProject;
    base?: number | null;
    revise?: typeof reviseProject;
    onKeepAndOpen?: (latest: number) => Promise<'opened' | 'notKept' | 'failed'>;
  } = {},
) {
  const list = vi.fn<typeof listProjects>().mockResolvedValue({
    kind: 'listed',
    page: {
      permissions: [],
      projects: [{ id: 'p1', name: 'Tower', revisions: 3, updated_at: 'now' }],
    },
  });
  const revise = vi.fn<typeof reviseProject>(options.revise ?? (() => Promise.resolve(STALE)));
  const onReconcile = vi.fn();
  const onOpened = vi.fn();
  const onKeepAndOpen = vi.fn(options.onKeepAndOpen ?? (() => Promise.resolve('opened' as const)));
  const props = {
    token: 'tok',
    current: { name: 'Tower', request: REQUEST },
    opened: options.open ?? opened(2, [1, 2, 3]),
    dirty: true,
    onOpened,
    onReconcile,
    onKeepAndOpen,
    impls: { list, revise },
    ...(options.base !== undefined ? { base: options.base } : {}),
  };
  const view = renderApp(<ProjectsPanel {...props} />, { locale: 'en' });
  return { revise, onReconcile, onOpened, onKeepAndOpen, props, view };
}

function saveButton() {
  return screen.getByRole('button', { name: 'Save as new revision' });
}

describe('publishing base', () => {
  it('states the revision the work was made from, not the newest one seen since', async () => {
    // Revision 2 open; revision 3 was seen since (a refreshed list of revisions).
    const { revise } = setup({ revise: () => Promise.resolve(STALE) });
    fireEvent.click(await screen.findByRole('button', { name: 'Save as new revision' }));
    await waitFor(() => {
      expect(revise).toHaveBeenCalledTimes(1);
    });
    expect(revise.mock.calls[0]?.[0].expectedRevision).toBe(2);
  });

  it('uses an explicit base when the page gives one', async () => {
    const { revise } = setup({ base: 3, revise: () => Promise.resolve(STALE) });
    fireEvent.click(await screen.findByRole('button', { name: 'Save as new revision' }));
    await waitFor(() => {
      expect(revise).toHaveBeenCalledTimes(1);
    });
    expect(revise.mock.calls[0]?.[0].expectedRevision).toBe(3);
  });

  it('holds the save while the base is not provable', async () => {
    const { revise } = setup({ base: null });
    fireEvent.click(await screen.findByRole('button', { name: 'Save as new revision' }));
    expect((await screen.findByTestId('projects-message')).textContent).toMatch(
      /Saving is on hold/,
    );
    expect(revise).not.toHaveBeenCalled();
  });

  it('a stale save saves nothing, keeps the work, and offers explicit choices', async () => {
    const { revise, onReconcile, onOpened } = setup();
    fireEvent.click(await screen.findByRole('button', { name: 'Save as new revision' }));
    const region = await screen.findByTestId('stale-reconcile');
    expect(region.textContent).toMatch(/revision 3 was saved after revision 2/);
    expect(region.textContent).toMatch(/nothing was overwritten/);
    // Nothing applied: the page's project is not replaced.
    expect(onOpened).not.toHaveBeenCalled();
    // Reconciling needs its confirmation first.
    const accept = screen.getByTestId('stale-reconcile-accept');
    expect(accept.hasAttribute('disabled')).toBe(true);
    fireEvent.click(accept);
    expect(onReconcile).not.toHaveBeenCalled();
    fireEvent.click(screen.getByTestId('stale-confirm'));
    fireEvent.click(screen.getByTestId('stale-reconcile-accept'));
    expect(onReconcile).toHaveBeenCalledWith(3);
    // Reconciling publishes nothing by itself.
    expect(revise).toHaveBeenCalledTimes(1);
    expect(screen.getByTestId('projects-message').textContent).toMatch(/Press Save to publish/);
  });

  it('after an explicit reconciliation the next save states the newer base', async () => {
    const { revise, props, view } = setup();
    fireEvent.click(await screen.findByRole('button', { name: 'Save as new revision' }));
    await screen.findByTestId('stale-reconcile');
    fireEvent.click(screen.getByTestId('stale-confirm'));
    fireEvent.click(screen.getByTestId('stale-reconcile-accept'));
    // The page applies the reconciliation as the base it passes back.
    view.rerender(<ProjectsPanel {...props} base={3} />);
    fireEvent.click(saveButton());
    await waitFor(() => {
      expect(revise).toHaveBeenCalledTimes(2);
    });
    expect(revise.mock.calls[1]?.[0].expectedRevision).toBe(3);
  });

  it('"keep my changes and open the newer revision" asks the page, and says when nothing was kept', async () => {
    const { onKeepAndOpen } = setup({ onKeepAndOpen: () => Promise.resolve('notKept') });
    fireEvent.click(await screen.findByRole('button', { name: 'Save as new revision' }));
    fireEvent.click(await screen.findByTestId('stale-keep-open'));
    expect(onKeepAndOpen).toHaveBeenCalledWith(3);
    expect((await screen.findByTestId('projects-message')).textContent).toMatch(
      /Nothing was replaced/,
    );
  });

  it('a successful save answers the question: the next base is the new revision', async () => {
    const saved = { id: 'p1', revision: 3 } as unknown as SavedProject;
    const { onOpened, props, view, revise } = setup({
      revise: () => Promise.resolve({ kind: 'saved', project: saved }),
    });
    fireEvent.click(await screen.findByRole('button', { name: 'Save as new revision' }));
    await waitFor(() => {
      expect(onOpened).toHaveBeenCalledWith(saved, false);
    });
    view.rerender(<ProjectsPanel {...props} opened={opened(3, [1, 2, 3])} />);
    fireEvent.click(saveButton());
    await waitFor(() => {
      expect(revise).toHaveBeenCalledTimes(2);
    });
    expect(revise.mock.calls[1]?.[0].expectedRevision).toBe(3);
  });
});

describe('dialog focus', () => {
  function Harness({ removeOpener }: { removeOpener: boolean }) {
    const [open, setOpen] = useState(false);
    const [gone, setGone] = useState(false);
    const safe = useRef<HTMLButtonElement>(null);
    const fallback = useRef<HTMLHeadingElement>(null);
    return (
      <>
        <h2 ref={fallback} tabIndex={-1}>
          Panel
        </h2>
        {!gone ? (
          <button
            type="button"
            onClick={() => {
              setOpen(true);
            }}
          >
            Opener
          </button>
        ) : null}
        <Dialog
          open={open}
          onClose={() => {
            setOpen(false);
          }}
          initialFocus={safe}
          onClosed={(returned) => {
            if (!returned) fallback.current?.focus();
          }}
          title="Conflict"
          closeLabel="Close"
          footer={
            <>
              <button
                ref={safe}
                type="button"
                onClick={() => {
                  if (removeOpener) setGone(true);
                  setOpen(false);
                }}
              >
                Cancel
              </button>
              <button type="button">Replace</button>
            </>
          }
        >
          <p>Body</p>
        </Dialog>
      </>
    );
  }

  it('focuses the safe choice on opening and returns focus to the opener', async () => {
    renderApp(<Harness removeOpener={false} />, { locale: 'en' });
    const opener = screen.getByRole('button', { name: 'Opener' });
    opener.focus();
    fireEvent.click(opener);
    await waitFor(() => {
      expect(document.activeElement?.textContent).toBe('Cancel');
    });
    act(() => {
      (document.activeElement as HTMLButtonElement).click();
    });
    await waitFor(() => {
      expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Opener' }));
    });
  });

  it('keeps Tab and Shift+Tab inside the dialog', async () => {
    renderApp(<Harness removeOpener={false} />, { locale: 'en' });
    fireEvent.click(screen.getByRole('button', { name: 'Opener' }));
    const replace = await screen.findByRole('button', { name: 'Replace' });
    replace.focus();
    fireEvent.keyDown(replace, { key: 'Tab' });
    expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Close' }));
    fireEvent.keyDown(document.activeElement as HTMLElement, { key: 'Tab', shiftKey: true });
    expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Replace' }));
  });

  it('places focus on the panel when the opener is gone after closing', async () => {
    renderApp(<Harness removeOpener />, { locale: 'en' });
    const opener = screen.getByRole('button', { name: 'Opener' });
    opener.focus();
    fireEvent.click(opener);
    await waitFor(() => {
      expect(document.activeElement?.textContent).toBe('Cancel');
    });
    act(() => {
      (document.activeElement as HTMLButtonElement).click();
    });
    await waitFor(() => {
      expect(document.activeElement?.textContent).toBe('Panel');
    });
  });
});
