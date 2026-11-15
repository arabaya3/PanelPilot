import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';

/**
 * The project workspace's views, in tab order. `/design` with no `?view=`
 * opens on the overview, so every existing link to the page still lands
 * where it did.
 */
export const VIEWS = [
  'overview',
  'schedule',
  'drawings',
  'layout',
  'materials',
  'revisions',
  'review',
] as const;
export type View = (typeof VIEWS)[number];

export const DEFAULT_VIEW: View = 'overview';

/** The view a query string names; the overview for none or an unknown one. */
export function viewFromSearch(search: string): View {
  const named = new URLSearchParams(search).get('view');
  return VIEWS.find((view) => view === named) ?? DEFAULT_VIEW;
}

/**
 * The URL for a view, keeping every other query parameter and the hash. The
 * default view drops the parameter, so the plain `/design` stays canonical.
 */
export function urlForView(href: string, view: View): string {
  const url = new URL(href);
  if (view === DEFAULT_VIEW) url.searchParams.delete('view');
  else url.searchParams.set('view', view);
  return `${url.pathname}${url.search}${url.hash}`;
}

/** Zoom steps the drawing preview offers, as fractions of its natural size. */
export const ZOOM_STEPS = [0.5, 0.75, 1, 1.25, 1.5, 2] as const;

/** The next zoom step in a direction from `current`, clamped to the range. */
export function stepZoom(current: number, direction: 1 | -1): number {
  if (direction === 1) return ZOOM_STEPS.find((step) => step > current + 1e-9) ?? current;
  return [...ZOOM_STEPS].reverse().find((step) => step < current - 1e-9) ?? current;
}

/**
 * The scale that fits content of `contentWidth` into `frameWidth`, between
 * the smallest step and 1 (fit never enlarges). 1 when either is unknown.
 */
export function fitZoom(frameWidth: number, contentWidth: number): number {
  if (!(frameWidth > 0) || !(contentWidth > 0)) return 1;
  const smallest = ZOOM_STEPS[0];
  return Math.min(1, Math.max(smallest, frameWidth / contentWidth));
}

/** Marks a history entry the workspace pushed itself (a view, or the guard's buffer). */
const WORKSPACE_ENTRY = '__panelpilotWorkspace';
/** Marks the guard's buffer: the same URL again, pushed only so Back can be caught. */
const BUFFER_ENTRY = '__panelpilotUnsavedBuffer';

function isWorkspaceEntry(state: unknown): boolean {
  return typeof state === 'object' && state !== null && WORKSPACE_ENTRY in state;
}

function isBufferEntry(state: unknown): boolean {
  return typeof state === 'object' && state !== null && BUFFER_ENTRY in state;
}

function bufferEntryState(): Record<string, unknown> {
  return { ...workspaceEntryState(), [BUFFER_ENTRY]: true };
}

/**
 * The state for a history entry the workspace pushes: the router's own state
 * (so Back and Forward still restore this page) plus the workspace's mark.
 */
export function workspaceEntryState(): Record<string, unknown> {
  const current: unknown = window.history.state;
  const base: Record<string, unknown> =
    typeof current === 'object' && current !== null ? { ...current } : {};
  // Pushed from the buffer, an entry would otherwise be taken for the buffer
  // and stepped back over once the work is clean.
  const { [BUFFER_ENTRY]: _buffer, ...rest } = base;
  return { ...rest, [WORKSPACE_ENTRY]: true };
}

/** Go to a view with its own history entry, so Back and Forward move between views. */
export function pushView(view: View): void {
  const url = urlForView(window.location.href, view);
  const here = `${window.location.pathname}${window.location.search}${window.location.hash}`;
  if (url === here) return;
  window.history.pushState(workspaceEntryState(), '', url);
}

/** The same-origin page a click would leave for, or null when it would not leave this one. */
export function leavingHref(event: MouseEvent): string | null {
  if (event.defaultPrevented || event.button !== 0) return null;
  if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return null;
  const target = event.target;
  const link = target instanceof Element ? target.closest('a[href]') : null;
  if (!(link instanceof HTMLAnchorElement)) return null;
  if ((link.target && link.target !== '_self') || link.hasAttribute('download')) return null;
  const url = new URL(link.href, window.location.href);
  if (url.origin !== window.location.origin) return null;
  // A link to this very page (or a hash on it) loses nothing.
  if (url.pathname === window.location.pathname && url.search === window.location.search) {
    return null;
  }
  return url.href;
}

export type UnsavedChangesGuard = {
  /** True while the engineer is being asked whether to discard their changes. */
  asking: boolean;
  /** Run `action` now if nothing is unsaved; otherwise ask first. */
  guard: (action: () => void) => void;
  /** Discard the changes and carry on with what was asked. */
  discard: () => void;
  /** Keep the changes and stay. */
  stay: () => void;
};

/**
 * Keeps unsaved work from being lost without a word while `dirty`: work
 * entered and not saved as a project is kept nowhere else.
 *
 * - Reloading or closing the tab: the browser's own confirmation.
 * - A link to another page (the rail, the logo): the click is held and the
 *   engineer asked; `leave` follows the link once they choose to discard.
 * - Back out of the page: while dirty, an entry for this page sits above the
 *   one it was opened on. Back onto that entry puts it back and asks; to
 *   discard, the history goes back past it.
 * - A jump straight to another page's entry (several at once, from the
 *   browser's history menu) cannot be held; the page keeps a draft for that
 *   (see `draft.ts`).
 * - Anything else that would replace the work (opening a project) asks
 *   through `guard`.
 */
export function useUnsavedChangesGuard(
  dirty: boolean,
  leave: (href: string) => void = followLink,
): UnsavedChangesGuard {
  const [pending, setPending] = useState<{ run: () => void } | null>(null);
  const dirtyRef = useRef(dirty);
  // Set once the engineer chose to leave, so the browser does not ask again.
  const leaving = useRef(false);
  // Set while the guard steps back over its own buffer, so that pop is not
  // mistaken for the engineer pressing Back.
  const unwinding = useRef(false);
  // Set while a buffer this page pushed may be the current entry. A page
  // opened onto a buffer left by an earlier visit (Forward into it) does not
  // own it, and stepping back from there would leave the page.
  const ownsBuffer = useRef(false);
  useLayoutEffect(() => {
    dirtyRef.current = dirty;
  });

  useEffect(() => {
    if (!dirty) return;
    function warn(event: BeforeUnloadEvent) {
      if (leaving.current) return;
      event.preventDefault();
    }
    window.addEventListener('beforeunload', warn);
    return () => {
      window.removeEventListener('beforeunload', warn);
    };
  }, [dirty]);

  // While dirty, Back must land on an entry of this page that the guard can
  // tell apart from the one the page was opened on.
  //
  // Once the work is clean again (saved, or edited back), the buffer would
  // make the next Back land on the same page and appear to do nothing; step
  // back over it while it is still the current entry. A view pushed above it
  // since is a real entry, so the buffer is then left where it is.
  useEffect(() => {
    if (dirty && !isWorkspaceEntry(window.history.state)) {
      window.history.pushState(bufferEntryState(), '', window.location.href);
      ownsBuffer.current = true;
    } else if (!dirty && ownsBuffer.current && isBufferEntry(window.history.state)) {
      ownsBuffer.current = false;
      unwinding.current = true;
      window.history.back();
    }
  }, [dirty]);

  useEffect(() => {
    const home = window.location.pathname;
    function onClick(event: MouseEvent) {
      if (!dirtyRef.current || leaving.current) return;
      const href = leavingHref(event);
      if (href === null) return;
      // Captured on the document, ahead of the router's own link handler.
      event.preventDefault();
      event.stopPropagation();
      setPending({
        run: () => {
          leaving.current = true;
          leave(href);
        },
      });
    }
    function onPopState(event: PopStateEvent) {
      if (unwinding.current) {
        unwinding.current = false;
        return;
      }
      if (!dirtyRef.current || leaving.current || isWorkspaceEntry(event.state)) return;
      // A jump of several entries (the browser's history menu) can land on
      // another page outright. The router is already showing it and a
      // traversal cannot be cancelled, so nothing is pushed: an entry for this
      // page under that page's address would only corrupt the history. The
      // work survives as the page's draft, offered back on return.
      if (window.location.pathname !== home) return;
      // Back onto the entry the page was opened on: put the buffer back, then ask.
      window.history.pushState(bufferEntryState(), '', window.location.href);
      ownsBuffer.current = true;
      setPending({
        run: () => {
          leaving.current = true;
          window.history.go(-2);
        },
      });
    }
    document.addEventListener('click', onClick, true);
    window.addEventListener('popstate', onPopState);
    return () => {
      document.removeEventListener('click', onClick, true);
      window.removeEventListener('popstate', onPopState);
    };
  }, [leave]);

  const guard = useCallback((action: () => void) => {
    if (dirtyRef.current) setPending({ run: action });
    else action();
  }, []);

  return {
    asking: pending !== null,
    guard,
    discard: () => {
      const action = pending;
      setPending(null);
      action?.run();
    },
    stay: () => {
      setPending(null);
    },
  };
}

function followLink(href: string): void {
  window.location.assign(href);
}
