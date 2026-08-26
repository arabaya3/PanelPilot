import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import type { components } from '@panelpilot/shared-types';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

import { Chat } from '@/components/chat';
import { DiagnosticCard } from '@/components/diagnostic-card';
import { StateIcon, type StateShape } from '@/components/state-icon';
import type { StreamEvent } from '@/lib/diagnosis-stream';

import { renderApp } from './helpers';

/**
 * FE-014: the three states must be distinguishable without reading the text.
 *
 * That is the acceptance criterion, and it is worth being literal about what
 * it rules out — a state whose only difference is its wording fails, however
 * well-worded. So these assert on shape and chrome, never on a string.
 *
 * The audit that produced this pass found three real collisions rather than
 * cosmetic ones:
 *
 *   - uncertain and refusal rendered identically (both `SEVERITY_CLASSES.info`,
 *     same border, same surface, and the refusal had no badge at all)
 *   - a failed turn wore `severity-warning` — the exact chrome of a
 *     *confident* warning-severity diagnosis, so a turn that failed looked
 *     like an answer that arrived
 *   - nothing carried a shape, so every distinction rested on colour
 */

type DiagnosticResponse = components['schemas']['DiagnosticResponse'];

const CITATION = {
  document_id: 'doc-1',
  document_title: 'ACS880 Firmware Manual',
  manufacturer: 'ABB',
  page: 214,
  section: '6.3',
};

function response(overrides: Partial<DiagnosticResponse> = {}): DiagnosticResponse {
  return {
    session_id: 's1',
    answer: { text: 'x', citations: [CITATION] },
    diagnosis: {
      summary: 'The drive tripped on DC bus undervoltage.',
      summary_citation_ids: ['doc-1'],
      severity: 'critical',
      equipment_model: null,
      steps: [
        {
          order: 1,
          instruction: 'Measure the supply voltage.',
          rationale: 'r',
          citation_ids: ['doc-1'],
          severity: 'critical',
        },
      ],
    },
    confidence: {
      overall: 0.9,
      retrieval_score: 0.9,
      passage_agreement: 0.9,
      citation_density: 0.9,
    },
    low_confidence: false,
    refusal_message: null,
    ...overrides,
  };
}

/**
 * One icon's outline, as a canonical point set.
 *
 * A `<rect>` and a `<polygon>` tracing the same square are the same shape to
 * an eye and different strings to a string compare, so the comparison has to
 * happen on points.
 */
function normalise(svg: SVGElement | null): string {
  const shape = svg?.firstElementChild;
  if (!shape) return '';

  if (shape.tagName === 'circle') {
    const [cx, cy, r] = ['cx', 'cy', 'r'].map((a) => Number(shape.getAttribute(a) ?? 0));
    return `circle:${String(cx)},${String(cy)},${String(r)}`;
  }
  if (shape.tagName === 'rect') {
    const read = (name: string) => Number(shape.getAttribute(name) ?? 0);
    const [x, y, w, h] = [read('x'), read('y'), read('width'), read('height')];
    // Expanded to its corners, so it compares directly against a polygon
    // drawing the same box.
    return points([
      [x, y],
      [x + w, y],
      [x + w, y + h],
      [x, y + h],
    ]);
  }
  return points(
    (shape.getAttribute('points') ?? '')
      .trim()
      .split(/\s+/)
      .map((pair) => pair.split(',').map(Number) as [number, number]),
  );
}

/** Corners, sorted, so winding order and start point do not matter. */
function points(corners: [number, number][]): string {
  return corners
    .map(([x, y]) => `${String(x)},${String(y)}`)
    .sort()
    .join(' ');
}

/** Every shape rendered inside one card. */
function shapesIn(card: HTMLElement): string[] {
  return [...card.querySelectorAll('[data-shape]')].map(
    (node) => node.getAttribute('data-shape') ?? '',
  );
}

// --- the shapes themselves ----------------------------------------------------

describe('StateIcon', () => {
  const SHAPES: StateShape[] = ['critical', 'warning', 'info', 'uncertain', 'error'];

  it.each(SHAPES)('renders a %s shape', (shape) => {
    const { container } = renderApp(<StateIcon shape={shape} />);
    expect(container.querySelector(`[data-shape="${shape}"]`)).toBeTruthy();
  });

  it('gives every state a different silhouette', () => {
    // Compared as geometry rather than as markup. The first version of this
    // test compared `innerHTML` strings, and a mutation that drew the diamond
    // as a polygon tracing the *exact* square of the error icon passed it —
    // two states that must not be confused rendering pixel-identical, with
    // the suite silent, because the strings differed.
    const drawn = SHAPES.map((shape) => {
      const { container, unmount } = renderApp(<StateIcon shape={shape} />);
      const outline = normalise(container.querySelector('svg'));
      unmount();
      return outline;
    });
    expect(new Set(drawn).size).toBe(SHAPES.length);
  });

  it('is hidden from assistive technology', () => {
    // Every icon sits beside a text label that already says the same thing.
    // Announcing "triangle" adds nothing a screen-reader user can act on.
    const { container } = renderApp(<StateIcon shape="warning" />);
    const svg = container.querySelector('svg');
    expect(svg?.getAttribute('aria-hidden')).toBe('true');
    expect(svg?.getAttribute('focusable')).toBe('false');
  });

  it('scales with the text beside it', () => {
    // A pixel size stops matching the moment someone zooms, and this is read
    // on phones held at arm's length.
    const { container } = renderApp(<StateIcon shape="info" />);
    expect(container.querySelector('svg')?.getAttribute('width')).toBe('1em');
  });
});

// --- the three states, told apart without reading -----------------------------

describe('the three states are distinguishable without text', () => {
  function render(payload: DiagnosticResponse) {
    const { unmount } = renderApp(<DiagnosticCard response={payload} />);
    const card = screen.getByTestId('diagnostic-card');
    const result = {
      variant: card.getAttribute('data-variant'),
      shapes: shapesIn(card),
      className: card.className,
    };
    unmount();
    return result;
  }

  it('gives each state its own shape', () => {
    const confident = render(response());
    const uncertain = render(response({ low_confidence: true }));
    const refusal = render(
      response({ diagnosis: null, refusal_message: 'No passage supports it.' }),
    );

    expect(confident.shapes).toContain('critical');
    expect(uncertain.shapes).toContain('uncertain');
    expect(refusal.shapes).toContain('error');

    // And no two share one.
    expect(new Set([confident.shapes[0], uncertain.shapes[0], refusal.shapes[0]]).size).toBe(3);
  });

  it('stops the uncertain and refusal cards from rendering identically', () => {
    // They meant different things and looked the same: both info-severity,
    // same border, same surface, and the refusal carried no badge at all.
    const uncertain = render(response({ low_confidence: true }));
    const refusal = render(response({ diagnosis: null, refusal_message: 'No.' }));

    expect(uncertain.className).not.toBe(refusal.className);
    // A heavy leading rule marks the two states where nothing arrived. The
    // first version used a dashed border for this and claimed it was unique;
    // it was not — the file dropzone in the same chat surface already uses
    // `border-2 border-dashed`, so the signal was taken before it was
    // invented. Dashed still means 'empty, waiting to be filled' there.
    expect(refusal.className).toContain('border-s-8');
    expect(uncertain.className).not.toContain('border-s-8');
  });

  it('does not let an uncertain card borrow a confident card’s chrome', () => {
    // A critical-severity payload that is *not* confident must not wear the
    // critical card's colours — that was the original FE-004 rule and this
    // re-checks it now that shapes have been added on top.
    const uncertain = render(response({ low_confidence: true }));
    expect(uncertain.className).toContain('border-severity-info');
    expect(uncertain.className).not.toContain('border-severity-critical');
    expect(uncertain.shapes).not.toContain('critical');
  });

  it.each(['critical', 'warning', 'info'] as const)(
    'pairs the %s severity with its own shape, not only its colour',
    (severity) => {
      const payload = response();
      if (payload.diagnosis) payload.diagnosis.severity = severity;
      const rendered = render(payload);
      expect(rendered.shapes[0]).toBe(severity);
    },
  );
});

// --- the measured reason this pass exists -------------------------------------

describe('colour alone is not enough', () => {
  /**
   * Brettel/Viénot LMS simulation with CIE76 distance.
   *
   * Not a decoration on the test file: this is the evidence for the whole
   * task. The severity palette is measurably ambiguous under the commonest
   * deficiency, so a state distinguished only by colour is not distinguished.
   */
  const hex = (h: string) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16));
  const lin = (c: number) =>
    c / 255 <= 0.04045 ? c / 255 / 12.92 : ((c / 255 + 0.055) / 1.055) ** 2.4;
  const gam = (c: number) => (c <= 0.0031308 ? c * 12.92 : 1.055 * c ** (1 / 2.4) - 0.055);

  function toLms([r, g, b]: number[]): number[] {
    const [R, G, B] = [lin(r ?? 0), lin(g ?? 0), lin(b ?? 0)];
    return [
      0.31399022 * R + 0.63951294 * G + 0.04649755 * B,
      0.15537241 * R + 0.75789446 * G + 0.08670142 * B,
      0.01775239 * R + 0.10944209 * G + 0.87256922 * B,
    ];
  }

  function fromLms([l, m, s]: number[]): number[] {
    const [L, M, S] = [l ?? 0, m ?? 0, s ?? 0];
    return [
      5.47221206 * L - 4.6419601 * M + 0.16963708 * S,
      -1.1252419 * L + 2.29317094 * M - 0.1678952 * S,
      0.02980165 * L - 0.19318073 * M + 1.16364789 * S,
    ].map((c) => Math.max(0, Math.min(255, Math.round(gam(Math.max(0, c)) * 255))));
  }

  /**
   * Viénot's deuteranope substitution: M' = 0.494207·L + 1.24827·S.
   *
   * `m` is deliberately discarded — deuteranopia is the absence of the M cone,
   * so its response is reconstructed from L and S rather than read.
   *
   * The coefficients are the part to get right, and the first version of this
   * file got them wrong: it used 0.9513092 / 0.04866992, which sets M ≈ L and
   * is not the deuteranope row at all. The output gave that away — every
   * colour came back with R exactly equal to G and blue clamped to zero (it
   * went negative before clamping), which is a degenerate collapse onto a
   * single yellow line rather than a percept. Any two reds land near each
   * other under that, so the ΔE it produced was measuring the bug.
   */
  const deuteranopia = ([l, _m, s]: number[]) => [
    l ?? 0,
    0.494207 * (l ?? 0) + 1.24827 * (s ?? 0),
    s ?? 0,
  ];

  function toLab([r, g, b]: number[]): number[] {
    const [R, G, B] = [lin(r ?? 0), lin(g ?? 0), lin(b ?? 0)];
    const x = (0.4124 * R + 0.3576 * G + 0.1805 * B) / 0.95047;
    const y = 0.2126 * R + 0.7152 * G + 0.0722 * B;
    const z = (0.0193 * R + 0.1192 * G + 0.9505 * B) / 1.08883;
    const f = (t: number) => (t > 0.008856 ? Math.cbrt(t) : 7.787 * t + 16 / 116);
    return [116 * f(y) - 16, 500 * (f(x) - f(y)), 200 * (f(y) - f(z))];
  }

  function distance(a: number[], b: number[]): number {
    const [l1, a1, b1] = toLab(a);
    const [l2, a2, b2] = toLab(b);
    return Math.hypot((l1 ?? 0) - (l2 ?? 0), (a1 ?? 0) - (a2 ?? 0), (b1 ?? 0) - (b2 ?? 0));
  }

  /** A token's value, read from the stylesheet rather than restated here. */
  function token(name: string): string {
    const css = readFileSync(resolve(process.cwd(), 'src/styles/tokens.css'), 'utf8');
    const match = new RegExp(`${name}:\\s*(#[0-9a-f]{6})`, 'i').exec(css);
    if (!match?.[1]) throw new Error(`${name} not found`);
    return match[1];
  }

  it('sanity-checks the simulation before trusting a number out of it', () => {
    // The guard that would have caught the original error. A correct
    // round trip through LMS and back must return the colour unchanged — and
    // a *simulated* colour must not come back with R exactly equal to G and
    // blue at zero, which is what a degenerate transform produces and what a
    // real percept never does.
    const critical = hex(token('--color-severity-critical'));
    expect(fromLms(toLms(critical))).toEqual(critical);

    const simulated = simulate(token('--color-severity-critical'));
    const [r, g, b] = simulated;
    expect(r === g && b === 0, 'the simulation has collapsed to a single line').toBe(false);
  });

  it('confirms critical and warning stay close under deuteranopia', () => {
    // ΔE ≈ 11 against ≈ 27 for normal vision: less than half the separation,
    // for roughly one man in sixteen, in the pair where confusing the two
    // matters most. Low enough that colour should not be carrying this alone,
    // which is what the shapes are for.
    //
    // Bounded on both sides. The upper bound is the finding; the lower bound
    // is there because the first version of this file reported 6 from a
    // broken transform, and a number that drifts *down* is now as loud as one
    // that drifts up.
    const critical = simulate(token('--color-severity-critical'));
    const warning = simulate(token('--color-severity-warning'));
    const separation = distance(critical, warning);

    expect(separation).toBeLessThan(15);
    expect(separation, 'suspiciously low — check the simulation, not the palette').toBeGreaterThan(
      8,
    );
  });

  function simulate(colour: string): number[] {
    return fromLms(deuteranopia(toLms(hex(colour))));
  }

  it('confirms the shapes do not have that problem', () => {
    // Geometry has no colour to lose.
    const { container } = renderApp(
      <>
        <StateIcon shape="critical" />
        <StateIcon shape="warning" />
      </>,
    );
    const [first, second] = [...container.querySelectorAll('svg')];
    expect(first?.innerHTML).not.toBe(second?.innerHTML);
  });
});

// --- the badge still reads --------------------------------------------------

describe('the icon does not replace the words', () => {
  it('keeps the severity label beside the shape', () => {
    // Shape *and* text, not shape instead of text. A screen-reader user gets
    // the label; a colourblind user gets the silhouette; everyone else gets
    // both.
    renderApp(<DiagnosticCard response={response()} />);
    const card = screen.getByTestId('diagnostic-card');
    // Two of them, both deliberate: the card's badge and the per-step
    // sr-only severity that FE-004 added so a step's urgency is not carried
    // by its border colour alone.
    expect(within(card).getAllByText('Critical').length).toBeGreaterThanOrEqual(1);
    expect(shapesIn(card)).toContain('critical');
  });
});

// --- the hard error, in the transcript ----------------------------------------

describe('a failed turn does not look like an answer', () => {
  /** Drive a real turn to failure and return the rendered card. */
  async function failedTurn() {
    const stream = (): AsyncGenerator<StreamEvent> =>
      (async function* () {
        await Promise.resolve();
        yield { kind: 'interrupted', reason: 'connection-lost' };
      })();

    renderApp(<Chat token="t" streamImpl={stream} />);
    const input = document.getElementById('chat-input') as HTMLInputElement;
    fireEvent.change(input, { target: { value: 'Why is it tripping?' } });
    fireEvent.submit(input.closest('form') as HTMLFormElement);

    await waitFor(() => {
      expect(screen.getByTestId('assistant-failure')).toBeTruthy();
    });
    return screen.getByTestId('assistant-failure');
  }

  it('no longer wears the chrome of a confident warning diagnosis', async () => {
    // The collision this pass fixed, asserted on what actually renders rather
    // than on the source. A failed turn used `border-severity-warning` on
    // `bg-severity-warning-surface` — exactly what a *confident*
    // warning-severity diagnosis wears. The spec asks for hard error to be
    // distinct from uncertain; it was in fact colliding with confident, which
    // is worse: a turn that failed looked like an answer that arrived.
    const failure = await failedTurn();

    expect(failure.className).not.toContain('bg-severity-warning-surface');
    expect(failure.className).not.toContain('border-severity-warning');
  });

  it('carries a square, which nothing else does', async () => {
    const failure = await failedTurn();
    const shapes = [...failure.querySelectorAll('[data-shape]')].map((node) =>
      node.getAttribute('data-shape'),
    );
    expect(shapes).toContain('error');
  });

  it('carries the same leading rule as the refusal', async () => {
    // A heavy leading rule means nothing arrived — a refusal or a failure.
    // Without it, an answer is present, whatever its confidence. One bit,
    // readable down a transcript at a glance.
    const failure = await failedTurn();
    expect(failure.className).toContain('border-s-8');
  });
});

// --- the steps, not only the badge --------------------------------------------

describe('a step carries its severity as a shape too', () => {
  it('gives every step an icon, not just the card header', () => {
    // The pass's own argument, applied where it was first left out. A step's
    // urgency lived in a 4px border colour — the same three colours this file
    // measures as ambiguous — with an sr-only label for screen readers and
    // nothing at all for a sighted colourblind engineer.
    const payload = response();
    if (payload.diagnosis) {
      const [first] = payload.diagnosis.steps;
      if (!first) throw new Error('fixture has no step');
      payload.diagnosis.steps = [
        { ...first, order: 1, severity: 'critical' },
        { ...first, order: 2, severity: 'warning' },
      ];
    }
    renderApp(<DiagnosticCard response={payload} />);

    // Scoped to the ordered step list: the citation blocks inside each step
    // are list items too.
    const list = screen.getByTestId('diagnostic-card').querySelector('ol');
    const steps = [...(list?.children ?? [])];
    expect(steps).toHaveLength(2);
    for (const [index, step] of steps.entries()) {
      const shapes = [...step.querySelectorAll('[data-shape]')].map((node) =>
        node.getAttribute('data-shape'),
      );
      expect(shapes, `step ${String(index + 1)} has no severity shape`).toContain(
        index === 0 ? 'critical' : 'warning',
      );
    }
  });
});
