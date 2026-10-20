import { useEffect } from 'react';

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

/**
 * Asks the browser to confirm leaving while `dirty`: work entered and not
 * saved as a project would otherwise be lost, since nothing is kept in storage.
 */
export function useUnsavedChangesGuard(dirty: boolean): void {
  useEffect(() => {
    if (!dirty) return;
    function warn(event: BeforeUnloadEvent) {
      event.preventDefault();
    }
    window.addEventListener('beforeunload', warn);
    return () => {
      window.removeEventListener('beforeunload', warn);
    };
  }, [dirty]);
}
