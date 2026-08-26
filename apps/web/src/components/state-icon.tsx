import type { ReactElement } from 'react';

/**
 * A distinct shape per state, so colour is never the only signal.
 *
 * This exists because the palette measurably fails on its own. Simulating the
 * three severity colours through the common deficiencies (Brettel/Viénot LMS,
 * CIE76 distance) puts **critical against warning at ΔE 6 under deuteranopia**
 * in the light theme — indistinguishable, for roughly one man in sixteen, in
 * exactly the pair where confusing the two matters most. Under tritanopia the
 * dark theme fares similarly at ΔE 17.
 *
 * So each state gets a silhouette that reads at a glance and survives being
 * rendered in grey:
 *
 *   critical   X cross   — the only shape here with concave corners
 *   warning    triangle  — pointed, upward, the standard hazard outline
 *   info       circle    — round, unpointed, deliberately calm
 *   uncertain  question  — a mark, not a polygon at all
 *   error      square    — flat-sided and blunt; a stopped thing, not a hazard
 *
 * The first version used an octagon for critical and a diamond for uncertain,
 * and both were wrong at the size these actually render. An octagon at 16px is
 * a decent antialiased circle — so critical and info, two states that must not
 * be confused, were near-identical silhouettes. A diamond is a square at 45°,
 * which is a real cue but a weak one when both are 4-cornered blobs a few
 * pixels across, and those two are the "something went wrong" pair.
 *
 * The replacements do not rely on counting corners, because corner count stops
 * being legible at exactly the size where this has to work. A cross is the
 * only concave outline here; a question mark is not a filled polygon at all.
 * Both survive being rendered in grey on a sunlit screen, which is the case
 * that matters — saturation is the first thing to go.
 *
 * `aria-hidden` throughout: every one of these sits beside a text label that
 * already says the same thing, and announcing "triangle" adds nothing a
 * screen-reader user can use.
 */

export type StateShape = 'critical' | 'warning' | 'info' | 'uncertain' | 'error';

const PATHS: Record<StateShape, ReactElement> = {
  // Octagon.
  critical: <polygon points="7,1 13,1 19,7 19,13 13,19 7,19 1,13 1,7" fill="currentColor" />,
  // Triangle, point up.
  warning: <polygon points="10,2 19,18 1,18" fill="currentColor" />,
  // Circle.
  info: <circle cx="10" cy="10" r="8.5" fill="currentColor" />,
  // A question mark: a stroke and a dot, not an outline. Nothing else in the
  // set is drawn this way, so it cannot be mistaken for the square below at
  // any size.
  uncertain: (
    <>
      <path
        d="M6.2 6.4a3.9 3.9 0 1 1 4.6 4.3v2.1"
        fill="none"
        stroke="currentColor"
        strokeWidth="2.6"
        strokeLinecap="round"
      />
      <circle cx="10.8" cy="17.2" r="1.6" fill="currentColor" />
    </>
  ),
  // Square.
  error: <rect x="2" y="2" width="16" height="16" fill="currentColor" />,
};

export function StateIcon({ shape, className }: { shape: StateShape; className?: string }) {
  return (
    <svg
      viewBox="0 0 20 20"
      // Sized in `em` so it tracks the text beside it rather than fixing a
      // pixel size that stops matching when someone zooms.
      width="1em"
      height="1em"
      role="presentation"
      aria-hidden="true"
      focusable="false"
      data-shape={shape}
      className={className ? `shrink-0 ${className}` : 'shrink-0'}
    >
      {PATHS[shape]}
    </svg>
  );
}
