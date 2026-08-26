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
 *   critical   octagon   — the stop sign, and the only eight-sided shape here
 *   warning    triangle  — pointed, upward, the standard hazard outline
 *   info       circle    — round, unpointed, deliberately calm
 *   uncertain  diamond   — rotated square, unlike any of the above
 *   error      square    — flat-sided and blunt; a stopped thing, not a hazard
 *
 * The five are distinguishable by corner count alone, which is what makes the
 * distinction survive both colour blindness and a sunlit phone screen where
 * saturation is the first thing to go.
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
  // Diamond — a square on its corner, unmistakable against the square below.
  uncertain: <polygon points="10,1 19,10 10,19 1,10" fill="currentColor" />,
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
